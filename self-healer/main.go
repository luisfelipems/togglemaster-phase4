// self-healer — recebe webhooks do Grafana Alerting e reinicia o deployment
// indicado nas labels do alerta (Self-Healing).
//
// Payload recebido (formato do Grafana Alerting / Alertmanager webhook):
//
//	{
//	  "status": "firing",
//	  "alerts": [
//	    {
//	      "status": "firing",
//	      "labels": { "namespace": "togglemaster", "deployment": "auth-service", ... }
//	    }
//	  ]
//	}
//
// Para cada alerta "firing" com as labels namespace+deployment, executa um
// "rollout restart" equivalente: patch na anotação kubectl.kubernetes.io/restartedAt.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"time"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
)

type webhookAlert struct {
	Status string            `json:"status"`
	Labels map[string]string `json:"labels"`
}

type webhookPayload struct {
	Status string         `json:"status"`
	Alerts []webhookAlert `json:"alerts"`
}

func main() {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	mux.HandleFunc("/alert", handleAlert)
	log.Println("self-healer ouvindo em :8080")
	if err := http.ListenAndServe(":8080", mux); err != nil {
		log.Fatal(err)
	}
}

func handleAlert(w http.ResponseWriter, r *http.Request) {
	var payload webhookPayload
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		http.Error(w, "payload inválido", http.StatusBadRequest)
		return
	}

	config, err := rest.InClusterConfig()
	if err != nil {
		log.Printf("InClusterConfig: %v", err)
		http.Error(w, "sem acesso ao cluster", http.StatusInternalServerError)
		return
	}
	clientset, err := kubernetes.NewForConfig(config)
	if err != nil {
		log.Printf("kubernetes.NewForConfig: %v", err)
		http.Error(w, "sem cliente Kubernetes", http.StatusInternalServerError)
		return
	}

	restarted := 0
	for _, alert := range payload.Alerts {
		if alert.Status != "firing" {
			continue
		}
		ns := alert.Labels["namespace"]
		dep := alert.Labels["deployment"]
		if ns == "" || dep == "" {
			log.Printf("ignorando alerta sem labels namespace/deployment: %+v", alert.Labels)
			continue
		}
		if err := rolloutRestart(clientset, ns, dep); err != nil {
			log.Printf("ERRO ao reiniciar %s/%s: %v", ns, dep, err)
			continue
		}
		log.Printf("SELF-HEALING: rollout restart disparado para deployment %s/%s", ns, dep)
		restarted++
	}

	fmt.Fprintf(w, "ok: %d deployment(s) reiniciado(s)", restarted)
}

// rolloutRestart equivale a: kubectl rollout restart deployment/<name> -n <ns>
// usando strategic merge patch na anotação restartedAt (força novo ReplicaSet).
//
// Se o deployment estiver com 0 réplicas (ex.: serviço derrubado), um restart
// sozinho não sobe nenhum pod; nesse caso a réplica é restaurada para 1 no mesmo patch.
func rolloutRestart(clientset kubernetes.Interface, ns, name string) error {
	dep, err := clientset.AppsV1().Deployments(ns).Get(context.Background(), name, metav1.GetOptions{})
	if err != nil {
		return err
	}
	replicas := ""
	if dep.Spec.Replicas != nil && *dep.Spec.Replicas == 0 {
		replicas = `"replicas":1,`
		log.Printf("SELF-HEALING: %s/%s estava com 0 réplicas — restaurando para 1", ns, name)
	}
	patch := fmt.Sprintf(
		`{"spec":{%s"template":{"metadata":{"annotations":{"kubectl.kubernetes.io/restartedAt":"%s"}}}}}`,
		replicas,
		time.Now().UTC().Format(time.RFC3339),
	)
	_, err = clientset.AppsV1().Deployments(ns).Patch(
		context.Background(),
		name,
		types.StrategicMergePatchType,
		[]byte(patch),
		metav1.PatchOptions{},
	)
	return err
}

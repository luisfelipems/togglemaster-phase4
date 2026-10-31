#!/usr/bin/env bash
# =============================================================================
# Prova Real (Fase 4): derruba o flag-service e deixa a cadeia agir sozinha.
#   kube-state-metrics -> Grafana Alerting (Firing) -> PagerDuty + Discord
#   -> webhook self-healer -> deployment restaurado -> alerta Resolved.
# Pode ser executado quantas vezes quiser: cada execução gera um incidente novo.
# O auto-sync do Argo CD do app é pausado durante o teste (senão o selfHeal do
# Argo desfaz a falha antes do alerta) e restaurado ao final, mesmo com Ctrl+C.
# =============================================================================
set -euo pipefail

NS="${NS:-togglemaster}"
DEP="${DEP:-flag-service}"
APP="${APP:-flag-service}"
TIMEOUT_MIN="${TIMEOUT_MIN:-8}"

ORIG_SYNC=$(kubectl -n argocd get application "$APP" -o jsonpath='{.spec.syncPolicy}')
if [ -z "$ORIG_SYNC" ]; then
  echo "ERRO: não foi possível ler spec.syncPolicy da application $APP" >&2
  exit 1
fi

restore() {
  echo ">> Restaurando syncPolicy original do Argo CD ($APP)"
  kubectl -n argocd patch application "$APP" --type merge \
    -p "{\"spec\":{\"syncPolicy\":$ORIG_SYNC}}" >/dev/null
  kubectl -n argocd annotate application "$APP" argocd.argoproj.io/refresh=hard --overwrite >/dev/null
  kubectl -n argocd get application "$APP"
}
trap restore EXIT

echo ">> $(date -u +%H:%M:%SZ) Pausando auto-sync do Argo CD ($APP)"
kubectl -n argocd patch application "$APP" --type json \
  -p '[{"op":"remove","path":"/spec/syncPolicy/automated"}]' >/dev/null

echo ">> $(date -u +%H:%M:%SZ) FALHA PROPOSITAL: escalando $NS/$DEP para 0 réplicas"
kubectl -n "$NS" scale deploy/"$DEP" --replicas=0

echo ">> Aguardando o alerta disparar e o self-healer restaurar (até ${TIMEOUT_MIN} min)..."
deadline=$(( $(date +%s) + TIMEOUT_MIN * 60 ))
while :; do
  r=$(kubectl -n "$NS" get deploy "$DEP" -o jsonpath='{.spec.replicas}')
  if [ "${r:-0}" -ge 1 ]; then
    echo ">> $(date -u +%H:%M:%SZ) Self-healer restaurou $DEP (replicas=$r)"
    break
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "ERRO: self-healer não agiu em ${TIMEOUT_MIN} min. Verifique a regra no Grafana e os logs do self-healer." >&2
    exit 1
  fi
  sleep 10
done

kubectl -n "$NS" rollout status deploy/"$DEP" --timeout=180s
echo ">> $(date -u +%H:%M:%SZ) $DEP disponível. O alerta deve ir para Resolved em ~1-2 min."

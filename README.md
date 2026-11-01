# Tech Challenge Fase 4 — ToggleMaster
## Observabilidade Completa, Alerting Inteligente e Self-Healing em Kubernetes

---

## 👤 Participante

**Nome:** Luis Felipe Martins da Silva  
**RM:** 372751  
**Curso:** Pós-Graduação em Devops e Arquitetura Cloud  
**Instituição:** POSTECH - FIAP

---

## 🔗 Links dos Repositórios e Documentação

- **Repositório Principal (Código):** [https://github.com/luisfelipems/togglemaster-phase4](https://github.com/luisfelipems/togglemaster-phase4)
- **Repositório GitOps:** [https://github.com/luisfelipems/togglemaster-gitops](https://github.com/luisfelipems/togglemaster-gitops)
- **Vídeo de Demonstração:** _inserir link do vídeo da Fase 4_

---

## 📋 Resumo Executivo

Esta fase evolui o projeto ToggleMaster da Fase 3 para um ambiente com **observabilidade fim a fim**, incluindo:

- **Métricas** (Prometheus)
- **Logs centralizados** (Loki + Promtail)
- **Traces distribuídos** (OpenTelemetry + New Relic)
- **Alertas centralizados com múltiplos canais** (Grafana Alerting + PagerDuty + Discord)
- **Automação de resposta a incidentes** com **self-healing** via webhook no Kubernetes

A proposta principal da Fase 4 é provar, em ambiente real, o ciclo completo de operação:

**Incidente proposital → Alerta Firing → Notificação (PagerDuty + Discord) → Self-healing automático → Alerta Resolved**.

---

## 🧰 Tecnologias Utilizadas

| Categoria | Tecnologias |
|---|---|
| **Infraestrutura como Código** | Terraform, AWS (VPC, EKS, RDS, ElastiCache, DynamoDB, SQS, ECR, S3) |
| **Orquestração / GitOps** | Kubernetes (EKS), Argo CD |
| **CI/CD / DevSecOps** | GitHub Actions, Trivy, gosec, bandit, pip-audit |
| **Observabilidade (Métricas)** | Prometheus (kube-prometheus-stack) |
| **Observabilidade (Logs)** | Loki + Promtail |
| **Observabilidade (Visualização / Alertas)** | Grafana (dashboards + provisioning de alerting como código) |
| **Tracing Distribuído** | OpenTelemetry SDK (Go/Python) + OpenTelemetry Collector |
| **APM** | New Relic (região US) |
| **ChatOps / Incident Management** | Discord + PagerDuty |
| **Automação de Remediação** | Self-healer customizado em Go |
| **Linguagens** | Go 1.25 (auth, evaluation, self-healer), Python 3.11 (flag, targeting, analytics) |

---

## 🏗️ Arquitetura Implementada

### 1. Base da Plataforma (Fase 3)

A Fase 4 reaproveita a base provisionada na Fase 3:

- Cluster EKS em `sa-east-1`
- 5 microserviços (`auth`, `flag`, `targeting`, `evaluation`, `analytics`)
- RDS, Redis, DynamoDB, SQS e ECR
- GitOps com Argo CD

### 2. Camada de Observabilidade (Fase 4)

Aplicações de monitoramento gerenciadas no Argo CD:

- `monitoring-prometheus` (chart `kube-prometheus-stack`)
- `monitoring-loki` (chart `loki-stack`)
- `monitoring-grafana` (chart `grafana`)
- `monitoring-otel-collector` (manifests do repositório)
- `monitoring-self-healer` (manifests do repositório)

### 3. Fluxo Completo de Telemetria

```
Serviços (Go/Python com OTel SDK)
          │
          ▼
OpenTelemetry Collector (monitoring)
          │
          ├── Traces → New Relic APM
          └── Métricas → Prometheus/Grafana

Logs dos Pods → Promtail → Loki → Grafana Explore
```

### 4. Fluxo de Incidente e Auto-Remediação

```
Queda de réplicas de um deployment (ex.: flag-service)
          │
          ▼
Regra do Grafana Alerting detecta replicas_available < 1
          │
          ▼
Contact point "togglemaster-critico" dispara:
  - PagerDuty
  - Discord
  - Webhook para self-healer
          │
          ▼
Self-healer aplica patch no deployment:
  - Se replicas=0, restaura para 1
  - Dispara rollout restart
          │
          ▼
Serviço volta, Argo CD reconcilia para estado desejado (2 réplicas)
          │
          ▼
Alerta volta para Normal (Resolved)
```

---

## ⚙️ Componentes da Fase 4

### 1. Instrumentação OpenTelemetry nos microsserviços

Foi adicionada instrumentação OTel nos serviços para emissão de traces e métricas por OTLP, com variáveis como:

- `OTEL_SERVICE_NAME`
- `OTEL_EXPORTER_OTLP_ENDPOINT`

### 2. Grafana com Alerting como Código

O projeto usa provisioning em `applications-monitoring.yaml` para garantir persistência da configuração sem dependência de UI.

Itens provisionados:

- **Datasources** com `uid` fixo:
  - `Prometheus`
  - `Loki`
- **Contact point** `togglemaster-critico` com:
  - PagerDuty
  - Discord
  - Webhook para self-healer
- **Policy global** encaminhando eventos para `togglemaster-critico`
- **Regra** `tm-deploy-sem-replicas`:
  - folder: `ToggleMaster`
  - group: `tc4`
  - condição: deployment com réplicas disponíveis abaixo de 1 por 30s

### 3. Self-Healer (Go)

Serviço HTTP em `:8080` com endpoints:

- `/healthz`
- `/alert`

Comportamento no `/alert`:

1. Recebe payload do Grafana Alerting
2. Lê labels `namespace` e `deployment`
3. Se alerta estiver `firing`, executa patch no deployment
4. Se deployment estiver com 0 réplicas, restaura para 1
5. Dispara anotação de restart (`kubectl.kubernetes.io/restartedAt`)

---

## 🔁 CI/CD + GitOps (continuidade da Fase 3)

Cada serviço mantém pipeline dedicado no GitHub Actions, com estágios:

1. Build & Test
2. Lint
3. Security Scan (SCA + SAST)
4. Docker Build + Container Scan + Push ECR
5. Update GitOps

### Gates de Segurança

| Gate | Ferramenta | Severidade | Ação |
|---|---|---|---|
| SCA (dependências) | Trivy fs, pip-audit | CRITICAL | Falha pipeline |
| SAST (código) | gosec / bandit | HIGH+ | Falha pipeline |
| Container Scan | Trivy image | CRITICAL | Bloqueia push |

As imagens seguem padrão de tag com SHA para rastreabilidade:

`940104439733.dkr.ecr.sa-east-1.amazonaws.com/togglemaster/<serviço>:v1.0.0-<sha>`

---

## 🚀 Como Reproduzir o Ambiente da Fase 4

### 1. Pré-requisitos

- AWS CLI autenticado
- `kubectl`, `helm`, `terraform`, `docker`
- Acesso aos repositórios `togglemaster-phase4` e `togglemaster-gitops`

### 2. Garantir base da Fase 3 funcional

Validar cluster e aplicações principais:

```bash
kubectl get nodes
kubectl -n argocd get applications
kubectl -n togglemaster get deploy
```

### 3. Aplicar stack de monitoramento

```bash
kubectl apply -n argocd -f gitops/monitoring/applications-monitoring.yaml
kubectl apply -n argocd -f gitops/monitoring/applications-monitoring-extras.yaml
```

### 4. Criar secrets necessários no namespace `monitoring`

- `grafana-admin`
- `grafana-alerting` (`PAGERDUTY_INTEGRATION_KEY`, `DISCORD_WEBHOOK_URL`)
- `nr-secret` (ingest key do New Relic)

### 5. Validar saúde do ambiente

```bash
kubectl -n argocd get applications
kubectl -n monitoring get pods
kubectl -n togglemaster get deploy
```

Critérios esperados:

- Apps do Argo CD em `Synced` e `Healthy`
- Deployments dos 5 serviços em estado saudável
- Stack de monitoramento em `Running`
- Cluster com **3 nós**

---

## 🎬 Prova Real (Demonstração do Incidente)

Script de incidente:

```bash
bash scripts/incidente-flag-service.sh
```

Esse fluxo:

1. Pausa temporariamente o auto-sync do `flag-service`
2. Escala o deployment para 0
3. Aguarda alerta + ação do self-healer
4. Restaura syncPolicy original no final

Evidências esperadas:

- Regra no Grafana em **Firing**
- Incidente no PagerDuty
- Mensagem no Discord
- Log do self-healer (`restaurando para 1` + `rollout restart`)
- Retorno do deployment para estado saudável
- Regra em **Resolved/Normal**

---

## 📊 Evidências do Relatório

Checklist recomendado para o PDF:

- Dashboard customizado no Grafana
- Consulta de logs no Loki (Explore)
- Trace distribuído no New Relic APM
- Alerta Firing e depois Resolved
- Incidente PagerDuty + notificação Discord
- Logs do self-healer comprovando automação
- Estado final saudável no Argo CD

---

## ✅ Requisitos Atendidos na Fase 4

- [x] Observabilidade de métricas com Prometheus
- [x] Observabilidade de logs com Loki
- [x] Tracing distribuído com OpenTelemetry
- [x] Integração com APM (New Relic)
- [x] Alerting centralizado no Grafana
- [x] Notificações de incidente em múltiplos canais
- [x] Self-healing automático acionado por webhook
- [x] Configuração de alertas como código (provisioning)
- [x] Operação integrada com GitOps (Argo CD)

---

## 🎓 Aprendizados da Fase 4

1. **Observabilidade só é completa quando integra métricas, logs e traces** no mesmo fluxo operacional.
2. **Alerting como código elimina drift de configuração**, especialmente em Grafana sem PVC.
3. **Auto-remediação reduz MTTR**, transformando alertas em ações automáticas.
4. **GitOps + observabilidade** melhora auditoria, repetibilidade e confiabilidade operacional.

---

## 📝 Conclusão

A Fase 4 do ToggleMaster consolida uma evolução de maturidade operacional: além de entregar deploy contínuo e segurança no pipeline, o ambiente agora possui detecção inteligente de incidentes, notificação automática e remediação autônoma com evidência auditável.

Com isso, o projeto demonstra um cenário real de **SRE/DevOps moderno** em Kubernetes, com foco em disponibilidade, rastreabilidade e resposta rápida a falhas.

---

**Autor:** Luis Felipe Martins da Silva (RM372751)  
**Instituição:** POSTECH - FIAP

# Build em estágio duplo, sem CGO, imagem final mínima e sem root.
FROM golang:1.24-alpine AS builder
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /self-healer .

FROM alpine:3.20
RUN apk --no-cache add ca-certificates && adduser -D -u 10001 healer
USER 10001
COPY --from=builder /self-healer /self-healer
EXPOSE 8080
ENTRYPOINT ["/self-healer"]

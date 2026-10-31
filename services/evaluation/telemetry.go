package main

import (
	"context"
	"log"
	"os"
	"strings"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetricgrpc"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
	"go.opentelemetry.io/otel/sdk/metric"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.26.0"
)

func initTelemetry(defaultServiceName string) func() {
	ctx := context.Background()

	serviceName := os.Getenv("OTEL_SERVICE_NAME")
	if serviceName == "" {
		serviceName = defaultServiceName
	}

	endpoint := os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
	if endpoint == "" {
		endpoint = "otel-collector.monitoring:4317"
	}
	endpoint = strings.TrimPrefix(strings.TrimPrefix(endpoint, "http://"), "https://")

	res, err := resource.Merge(
		resource.Default(),
		resource.NewWithAttributes(
			semconv.SchemaURL,
			semconv.ServiceName(serviceName),
		),
	)
	if err != nil {
		log.Printf("OTel: erro ao montar resource: %v", err)
	}

	traceExporter, err := otlptracegrpc.New(
		ctx,
		otlptracegrpc.WithEndpoint(endpoint),
		otlptracegrpc.WithInsecure(),
	)
	if err != nil {
		log.Printf("OTel: erro ao criar trace exporter: %v", err)
		return func() {}
	}
	tracerProvider := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(traceExporter),
		sdktrace.WithResource(res),
	)
	otel.SetTracerProvider(tracerProvider)

	metricExporter, err := otlpmetricgrpc.New(
		ctx,
		otlpmetricgrpc.WithEndpoint(endpoint),
		otlpmetricgrpc.WithInsecure(),
	)
	if err != nil {
		log.Printf("OTel: erro ao criar metric exporter: %v", err)
		return func() { _ = tracerProvider.Shutdown(ctx) }
	}
	meterProvider := metric.NewMeterProvider(
		metric.WithReader(metric.NewPeriodicReader(metricExporter, metric.WithInterval(15*time.Second))),
		metric.WithResource(res),
	)
	otel.SetMeterProvider(meterProvider)

	return func() {
		_ = meterProvider.Shutdown(ctx)
		_ = tracerProvider.Shutdown(ctx)
	}
}

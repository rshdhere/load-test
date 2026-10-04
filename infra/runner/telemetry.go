package main

import (
	"context"
	"log/slog"
	"os"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
)

var tracer = otel.Tracer("o11y-runner")

// setupTelemetry sends logs to stdout as JSON (Alloy ships them to Loki) and,
// when OTEL_EXPORTER_OTLP_ENDPOINT is set, traces to Tempo. Log lines written
// inside a span carry its trace_id, which Grafana turns into a link to the trace.
func setupTelemetry(commit string) (shutdown func(context.Context) error) {
	slog.SetDefault(slog.New(traceHandler{slog.NewJSONHandler(os.Stdout, nil)}))

	if os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT") == "" {
		return func(context.Context) error { return nil }
	}
	ctx := context.Background()
	exporter, err := otlptracehttp.New(ctx)
	if err != nil {
		slog.Error("traces disabled: cannot create the OTLP exporter", "error", err)
		return func(context.Context) error { return nil }
	}
	res, err := resource.New(ctx, resource.WithAttributes(
		attribute.String("service.name", "runner"),
		attribute.String("service.version", commit),
	))
	if err != nil {
		slog.Error("cannot describe the runner for traces", "error", err)
	}
	provider := sdktrace.NewTracerProvider(sdktrace.WithBatcher(exporter), sdktrace.WithResource(res))
	otel.SetTracerProvider(provider)
	otel.SetTextMapPropagator(propagation.TraceContext{})
	return provider.Shutdown
}

// traceHandler adds the current span's trace_id and span_id to every log record.
type traceHandler struct{ slog.Handler }

func (h traceHandler) Handle(ctx context.Context, r slog.Record) error {
	if sc := trace.SpanContextFromContext(ctx); sc.IsValid() {
		r.AddAttrs(slog.String("trace_id", sc.TraceID().String()), slog.String("span_id", sc.SpanID().String()))
	}
	return h.Handler.Handle(ctx, r)
}

func (h traceHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	return traceHandler{h.Handler.WithAttrs(attrs)}
}

func (h traceHandler) WithGroup(name string) slog.Handler {
	return traceHandler{h.Handler.WithGroup(name)}
}

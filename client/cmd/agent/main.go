package main

import (
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"github.com/aiopsos/client/internal/logging"
)

func main() {
	cfg := logging.DefaultConfig()
	if v := os.Getenv("LOG_LEVEL"); v != "" {
		cfg.Level = parseLevel(v)
	}
	if v := os.Getenv("LOG_DIR"); v != "" {
		cfg.Dir = v
	}
	if v := os.Getenv("LOG_FORMAT"); v != "" {
		cfg.Format = v
	}

	log := logging.Setup(cfg)
	log.Info("AIOpsOS Go agent starting",
		"version", "0.1.0",
		"log_level", cfg.Level,
		"log_dir", cfg.Dir,
	)

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	sig := <-sigCh
	log.Info("agent shutting down", "signal", sig.String())
}

func parseLevel(s string) slog.Level {
	switch s {
	case "DEBUG":
		return slog.LevelDebug
	case "WARN":
		return slog.LevelWarn
	case "ERROR":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}

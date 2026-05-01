package logging

import (
	"io"
	"log/slog"
	"os"
	"path/filepath"

	"gopkg.in/natefinch/lumberjack.v2"
)

type Config struct {
	Level      slog.Level
	Dir        string
	Format     string
	MaxSizeMB  int
	MaxBackups int
	MaxAgeDays int
}

func DefaultConfig() Config {
	return Config{
		Level:      slog.LevelInfo,
		Dir:        "data/logs",
		Format:     "json",
		MaxSizeMB:  10,
		MaxBackups: 30,
		MaxAgeDays: 30,
	}
}

func Setup(cfg Config) *slog.Logger {
	level := new(slog.LevelVar)
	level.Set(cfg.Level)

	if err := os.MkdirAll(cfg.Dir, 0o755); err != nil {
		panic("failed to create log directory: " + err.Error())
	}

	logFile := &lumberjack.Logger{
		Filename:   filepath.Join(cfg.Dir, "agent.log"),
		MaxSize:    cfg.MaxSizeMB,
		MaxBackups: cfg.MaxBackups,
		MaxAge:     cfg.MaxAgeDays,
		Compress:   true,
	}

	writer := io.MultiWriter(os.Stdout, logFile)

	var handler slog.Handler
	opts := &slog.HandlerOptions{Level: level}
	if cfg.Format == "json" {
		handler = slog.NewJSONHandler(writer, opts)
	} else {
		handler = slog.NewTextHandler(writer, opts)
	}

	logger := slog.New(handler)
	slog.SetDefault(logger)
	return logger
}

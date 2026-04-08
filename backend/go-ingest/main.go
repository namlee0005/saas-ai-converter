package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
	"go.uber.org/zap"
	"nhooyr.io/websocket"
	"nhooyr.io/websocket/wsjson"
)

// ----------------------------------------------------------------------------
// Config
// ----------------------------------------------------------------------------

type Config struct {
	Addr       string // e.g. ":8080"
	RedisURL   string // e.g. "redis://localhost:6379"
	JWTSecret  string // shared with FastAPI
}

func configFromEnv() Config {
	return Config{
		Addr:      getenv("INGEST_ADDR", ":8080"),
		RedisURL:  getenv("REDIS_URL", "redis://localhost:6379"),
		JWTSecret: mustenv("JWT_SECRET"),
	}
}

// ----------------------------------------------------------------------------
// Domain types
// ----------------------------------------------------------------------------

// InboundMessage is sent by the widget over WebSocket.
type InboundMessage struct {
	Type      string          `json:"type"`       // "chat" | "ping"
	TenantID  uuid.UUID       `json:"tenant_id"`
	SessionID uuid.UUID       `json:"session_id"`
	LastMsgID string          `json:"last_msg_id,omitempty"` // for reconnect replay
	Payload   json.RawMessage `json:"payload"`
}

// OutboundMessage is pushed to the widget.
type OutboundMessage struct {
	Type    string          `json:"type"`    // "token" | "high_intent" | "tool_result" | "done"
	MsgID   string          `json:"msg_id"`
	Payload json.RawMessage `json:"payload"`
}

// Claims are embedded in the short-lived visitor JWT (issued by FastAPI).
type Claims struct {
	TenantID  uuid.UUID `json:"tenant_id"`
	SessionID uuid.UUID `json:"session_id"`
	jwt.RegisteredClaims
}

// ----------------------------------------------------------------------------
// Server
// ----------------------------------------------------------------------------

type Server struct {
	cfg    Config
	rdb    *redis.Client
	logger *zap.Logger
}

func NewServer(cfg Config, rdb *redis.Client, logger *zap.Logger) *Server {
	return &Server{cfg: cfg, rdb: rdb, logger: logger}
}

// ServeHTTP routes incoming HTTP requests.
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	switch r.URL.Path {
	case "/ws":
		s.handleWS(w, r)
	case "/healthz":
		w.WriteHeader(http.StatusOK)
	default:
		http.NotFound(w, r)
	}
}

// handleWS upgrades the connection, validates the JWT, then runs the relay loop.
func (s *Server) handleWS(w http.ResponseWriter, r *http.Request) {
	claims, err := s.validateJWT(r.URL.Query().Get("token"))
	if err != nil {
		s.logger.Warn("invalid JWT", zap.Error(err))
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}

	conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{
		// CSP: restrict to widget origin only (configured per tenant in prod)
		InsecureSkipVerify: false,
	})
	if err != nil {
		s.logger.Error("websocket accept failed", zap.Error(err))
		return
	}
	defer conn.CloseNow()

	ctx := r.Context()
	s.logger.Info("session connected",
		zap.String("tenant_id", claims.TenantID.String()),
		zap.String("session_id", claims.SessionID.String()),
	)

	// Subscribe to the per-session Redis pub/sub channel so outbound tokens
	// (produced by the Python RAG worker) reach this connection.
	pubsubKey := "session:" + claims.SessionID.String() + ":out"

	// Run inbound relay (widget → Redis) and outbound relay (Redis → widget) concurrently.
	errCh := make(chan error, 2)
	go func() { errCh <- s.relayInbound(ctx, conn, claims) }()
	go func() { errCh <- s.relayOutbound(ctx, conn, pubsubKey) }()

	if err := <-errCh; err != nil && !errors.Is(err, context.Canceled) {
		s.logger.Warn("session ended with error",
			zap.String("session_id", claims.SessionID.String()),
			zap.Error(err),
		)
	}

	conn.Close(websocket.StatusNormalClosure, "bye")
	s.logger.Info("session disconnected", zap.String("session_id", claims.SessionID.String()))
}

// relayInbound reads messages from the widget and publishes them to Redis Streams.
// The Go gateway has zero business logic — it is a pure relay.
func (s *Server) relayInbound(ctx context.Context, conn *websocket.Conn, claims *Claims) error {
	streamKey := claims.TenantID.String() + ":chat:in"
	for {
		var msg InboundMessage
		if err := wsjson.Read(ctx, conn, &msg); err != nil {
			return err
		}

		// Stamp tenant/session onto every message before forwarding.
		msg.TenantID = claims.TenantID
		msg.SessionID = claims.SessionID

		raw, err := json.Marshal(msg)
		if err != nil {
			s.logger.Error("marshal error", zap.Error(err))
			continue
		}

		if err := s.rdb.XAdd(ctx, &redis.XAddArgs{
			Stream: streamKey,
			Values: map[string]any{"data": string(raw)},
		}).Err(); err != nil {
			s.logger.Error("redis xadd failed", zap.Error(err))
			return err
		}
	}
}

// relayOutbound subscribes to a per-session Redis pub/sub channel and forwards
// messages produced by the Python AI worker back to the widget.
func (s *Server) relayOutbound(ctx context.Context, conn *websocket.Conn, channel string) error {
	sub := s.rdb.Subscribe(ctx, channel)
	defer sub.Close()

	ch := sub.Channel()
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case redisMsg, ok := <-ch:
			if !ok {
				return errors.New("redis pubsub channel closed")
			}
			var out OutboundMessage
			if err := json.Unmarshal([]byte(redisMsg.Payload), &out); err != nil {
				s.logger.Error("outbound unmarshal error", zap.Error(err))
				continue
			}
			if err := wsjson.Write(ctx, conn, &out); err != nil {
				return err
			}
		}
	}
}

// validateJWT parses and validates a short-lived visitor JWT.
func (s *Server) validateJWT(tokenStr string) (*Claims, error) {
	token, err := jwt.ParseWithClaims(tokenStr, &Claims{}, func(t *jwt.Token) (any, error) {
		if _, ok := t.Method.(*jwt.SigningMethodHMAC); !ok {
			return nil, errors.New("unexpected signing method")
		}
		return []byte(s.cfg.JWTSecret), nil
	})
	if err != nil || !token.Valid {
		return nil, errors.New("invalid token")
	}
	return token.Claims.(*Claims), nil
}

// ----------------------------------------------------------------------------
// Entrypoint
// ----------------------------------------------------------------------------

func main() {
	logger, _ := zap.NewProduction()
	defer logger.Sync()

	cfg := configFromEnv()

	opt, err := redis.ParseURL(cfg.RedisURL)
	if err != nil {
		logger.Fatal("invalid REDIS_URL", zap.Error(err))
	}
	rdb := redis.NewClient(opt)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := rdb.Ping(ctx).Err(); err != nil {
		logger.Fatal("redis ping failed", zap.Error(err))
	}

	srv := NewServer(cfg, rdb, logger)
	httpSrv := &http.Server{
		Addr:    cfg.Addr,
		Handler: srv,
		// Sane production timeouts — WS connections upgrade immediately so
		// ReadHeaderTimeout does not affect long-lived socket sessions.
		ReadHeaderTimeout: 10 * time.Second,
	}

	go func() {
		logger.Info("go-ingest listening", zap.String("addr", cfg.Addr))
		if err := httpSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Fatal("server error", zap.Error(err))
		}
	}()

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit

	shutCtx, shutCancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer shutCancel()
	logger.Info("shutting down gracefully")
	if err := httpSrv.Shutdown(shutCtx); err != nil {
		logger.Error("shutdown error", zap.Error(err))
	}
}

// ----------------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------------

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func mustenv(key string) string {
	v := os.Getenv(key)
	if v == "" {
		panic("required env var not set: " + key)
	}
	return v
}

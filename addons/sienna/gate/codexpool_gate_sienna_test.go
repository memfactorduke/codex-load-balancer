package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

// Native detection belongs to CPA. With cloaking disabled in the Claude config,
// the gate may admit a newer client without reading or rewriting its payload.
func TestCodexpoolGatePreservesUnrecognisedClaudeRequest(t *testing.T) {
	oldProfile := codexpoolProfile
	t.Cleanup(func() { codexpoolProfile = oldProfile })
	codexpoolProfile = "claude"
	for _, app := range []string{"cli"} {
		for _, route := range []string{"/v1/messages"} {
			t.Run(app+route, func(t *testing.T) {
				// Deliberately lacks the beta and metadata CPA's native detector needs.
				payload := `{"system":"Keep my instructions","messages":[{"role":"user","content":"hello"}]}`
				r := httptest.NewRequest(http.MethodPost, "http://127.0.0.1"+route, strings.NewReader(payload))
				r.Header.Set("User-Agent", "claude-cli/99.0.0 (external, cli)")
				r.Header.Set("X-App", app)
				r.Header.Set("Content-Type", "application/json")
				headers := r.Header.Clone()
				e := gin.New()
				codexpoolConfigureEngine(e)
				e.POST(route, func(c *gin.Context) {
					body, err := io.ReadAll(c.Request.Body)
					if err != nil || string(body) != payload {
						t.Fatalf("gate changed payload: %q, error: %v", body, err)
					}
					if !reflect.DeepEqual(c.Request.Header, headers) {
						t.Fatal("gate changed headers")
					}
					c.Status(http.StatusNoContent)
				})
				w := httptest.NewRecorder()
				e.ServeHTTP(w, r)
				if w.Code != http.StatusNoContent {
					t.Fatalf("got %d, want 204: %s", w.Code, w.Body.String())
				}
			})
		}
	}
}

// Use CPA's real native detector: a dummy downstream handler alone cannot
// establish whether CPA would reconstruct the client's identity.
func TestCodexpoolGateTokenCounter(t *testing.T) {
	oldProfile := codexpoolProfile
	t.Cleanup(func() { codexpoolProfile = oldProfile })
	codexpoolProfile = "claude"
	for _, tc := range []struct {
		name, app, ua, beta string
		want                int
	}{
		{"native", "cli", "claude-cli/2.1.283 (external, cli)", "claude-code-20250219", 204},
		{"background", "cli-bg", "claude-cli/2.1.283 (external, cli)", "claude-code-20250219", 403},
		{"new minor", "cli", "claude-cli/99.0.0 (external, cli)", "claude-code-20250219", 403},
		{"desktop", "cli", "claude-cli/2.1.281 (external, claude-desktop-3p)", "claude-code-20250219", 403},
		{"missing beta", "cli", "claude-cli/2.1.283 (external, cli)", "", 403},
	} {
		t.Run(tc.name, func(t *testing.T) {
			body := `{"model":"example","messages":[]}`
			r := httptest.NewRequest(http.MethodPost, "http://127.0.0.1/v1/messages/count_tokens", strings.NewReader(body))
			r.Header.Set("User-Agent", tc.ua)
			r.Header.Set("X-App", tc.app)
			r.Header.Set("Anthropic-Beta", tc.beta)
			headers := r.Header.Clone()
			e := gin.New()
			codexpoolConfigureEngine(e)
			e.POST("/v1/messages/count_tokens", func(c *gin.Context) {
				got, _ := io.ReadAll(c.Request.Body)
				if string(got) != body || !reflect.DeepEqual(headers, c.Request.Header) {
					t.Fatal("gate changed request")
				}
				c.Status(204)
			})
			w := httptest.NewRecorder()
			e.ServeHTTP(w, r)
			if w.Code != tc.want {
				t.Fatalf("got %d, want %d", w.Code, tc.want)
			}
		})
	}
}

// Exercise the actual middleware without opening a socket or reaching an upstream.
func TestCodexpoolGateClients(t *testing.T) {
	oldProfile := codexpoolProfile
	t.Cleanup(func() { codexpoolProfile = oldProfile })
	for _, tc := range []struct {
		name, profile, app, ua, origin, site, host, path string
		want                                             int
	}{
		{name: "desktop messages", profile: "claude", app: "cli", ua: "claude-cli/2.1.281 (external, claude-desktop-3p)", path: "/v1/messages", want: 204},
		{name: "foreground", profile: "claude", app: "cli", ua: "claude-cli/1.0", want: 204},
		{name: "background", profile: "claude", app: "cli-bg", ua: "claude-cli/1.0", want: 403},
		{name: "background missing UA", profile: "claude", app: "cli-bg", want: 403},
		{name: "background other UA", profile: "claude", app: "cli-bg", ua: "other/1.0", want: 403},
		{name: "missing app", profile: "claude", ua: "claude-cli/1.0", want: 403},
		{name: "unknown app", profile: "claude", app: "cli-other", ua: "claude-cli/1.0", want: 403},
		{name: "background browser", profile: "claude", app: "cli-bg", ua: "claude-cli/1.0", origin: "https://example.invalid", want: 403},
		{name: "background renderer", profile: "claude", app: "cli-bg", ua: "claude-cli/1.0", origin: "app://-", want: 403},
		{name: "background cross site", profile: "claude", app: "cli-bg", ua: "claude-cli/1.0", site: "cross-site", want: 403},
		{name: "background rebinding", profile: "claude", app: "cli-bg", ua: "claude-cli/1.0", host: "attacker.invalid", want: 403},
		{name: "management exempt", profile: "claude", path: "/v0/management/auth-files", want: 204},
		{name: "management traversal", profile: "claude", path: "/v0/management/../v1/models", want: 403},
		{name: "other path", profile: "claude", path: "/v1beta/models", want: 403},
		{name: "codex unchanged", want: 204},
		{name: "codex renderer", origin: "app://-", site: "cross-site", want: 204},
		{name: "codex browser", origin: "https://example.invalid", want: 403},
		{name: "unknown profile", profile: "typo", app: "cli-bg", ua: "claude-cli/1.0", want: 403},
	} {
		t.Run(tc.name, func(t *testing.T) {
			codexpoolProfile = tc.profile
			p := tc.path
			if p == "" {
				p = "/v1/models"
			}
			r := httptest.NewRequest(http.MethodGet, "http://127.0.0.1"+p, nil)
			for k, v := range map[string]string{"X-App": tc.app, "User-Agent": tc.ua, "Origin": tc.origin, "Sec-Fetch-Site": tc.site} {
				r.Header.Set(k, v)
			}
			if tc.host != "" {
				r.Host = tc.host
			}
			e := gin.New()
			codexpoolConfigureEngine(e)
			e.NoRoute(func(c *gin.Context) { c.Status(http.StatusNoContent) })
			w := httptest.NewRecorder()
			e.ServeHTTP(w, r)
			if w.Code != tc.want {
				t.Fatalf("got %d, want %d: %s", w.Code, tc.want, w.Body.String())
			}
		})
	}
}

// Arbitrary client versions must not grow the diagnostic map or log indefinitely.
func TestCodexpoolGateClientClassesBounded(t *testing.T) {
	oldProfile := codexpoolProfile
	t.Cleanup(func() { codexpoolProfile = oldProfile })
	codexpoolProfile = "claude"
	e := gin.New()
	codexpoolConfigureEngine(e)
	e.POST("/v1/messages", func(c *gin.Context) { c.Status(http.StatusNoContent) })
	for i := 0; i < 100; i++ {
		for _, suffix := range []string{"", " claude-desktop-3p"} {
			r := httptest.NewRequest(http.MethodPost, "http://127.0.0.1/v1/messages", nil)
			r.Header.Set("User-Agent", "claude-cli/"+strings.Repeat("x", i)+suffix)
			r.Header.Set("X-App", "cli")
			w := httptest.NewRecorder()
			e.ServeHTTP(w, r)
			if w.Code != http.StatusNoContent {
				t.Fatalf("gate changed admission: %d", w.Code)
			}
		}
	}
	count := 0
	codexpoolSeenClients.Range(func(key, value interface{}) bool { count++; return true })
	if count != 2 {
		t.Fatalf("unbounded seen-client map: %d", count)
	}
}

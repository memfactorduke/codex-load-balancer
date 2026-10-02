// codexpool_gate.go is added to CLIProxyAPI's cmd/server by `codexpool build`.
// It is the only custom request middleware: installed through the engine
// configurator hook, which runs before every CLIProxyAPI middleware (access log, CORS, request
// logging, auth, management IP-ban) and every route, WebSocket upgrades included.
//
// Why: codexpool runs the pool with api-keys: [] on 127.0.0.1 (the Codex app can only send its own
// ChatGPT bearer), and CLIProxyAPI answers every origin with Access-Control-Allow-Origin: *.
// Without this gate any web page could drive the pool through the browser, and five keyless
// management requests from a page would get 127.0.0.1 banned from the management API.
//
// A request passes only if its Host is a loopback name (defeats DNS rebinding) and it does not
// carry browser provenance (Origin, or Sec-Fetch-Site other than "none"). The Codex desktop app's
// own renderer origin (app://-) is allowed; the Codex Rust client sends neither header.
//
// Profiles register at process startup. The empty profile is the Codex pool;
// every unregistered profile refuses every request, including management.
package main

import (
	"net"
	"net/http"
	"os"
	"strings"

	"github.com/gin-gonic/gin"
	log "github.com/sirupsen/logrus"
)

const codexpoolAppOrigin = "app://-"

// Profiles can only accept or reject; they must not rewrite a request.
// Registration is init-only. Duplicate, empty or incomplete entries fail closed.
type codexpoolGateProfile struct {
	allowed   func(*http.Request) bool
	rejection string
	extra     gin.HandlerFunc
}

var codexpoolProfiles = map[string]codexpoolGateProfile{
	"": {allowed: func(*http.Request) bool { return true }},
}

func codexpoolRegisterProfile(name string, profile codexpoolGateProfile) {
	if _, exists := codexpoolProfiles[name]; exists || strings.TrimSpace(name) != name || name == "" || profile.allowed == nil {
		panic("codexpool: invalid or duplicate gate profile")
	}
	codexpoolProfiles[name] = profile
}

var codexpoolProfile = strings.TrimSpace(os.Getenv("CODEXPOOL_GATE_PROFILE"))

func codexpoolLoopbackHost(hostport string) bool {
	host := strings.ToLower(strings.TrimSpace(hostport))
	if h, _, err := net.SplitHostPort(host); err == nil {
		host = h
	}
	host = strings.Trim(host, "[]")
	return host == "127.0.0.1" || host == "localhost" || host == "::1"
}

func codexpoolGate() gin.HandlerFunc {
	return func(c *gin.Context) {
		r := c.Request
		origin := r.Header.Get("Origin")
		site := r.Header.Get("Sec-Fetch-Site")
		fromApp := origin == codexpoolAppOrigin && codexpoolProfile == ""
		browser := (origin != "" && !fromApp) || (site != "" && site != "none" && !fromApp)
		if !codexpoolLoopbackHost(r.Host) || browser {
			// Every field quoted: a page must not be able to forge log lines with %0A in the URL.
			log.Warnf("codexpool gate: rejected %q %q host=%q origin=%q sec-fetch-site=%q",
				r.Method, r.URL.EscapedPath(), r.Host, origin, site)
			c.AbortWithStatusJSON(http.StatusForbidden, gin.H{
				"error": "codexpool: only local non-browser clients may use this pool",
			})
			return
		}
		profile, registered := codexpoolProfiles[codexpoolProfile]
		if !registered || !profile.allowed(r) {
			log.Warnf("codexpool gate: rejected client %q %q profile=%q user-agent=%q x-app=%q",
				r.Method, r.URL.EscapedPath(), codexpoolProfile, r.Header.Get("User-Agent"), r.Header.Get("X-App"))
			message := profile.rejection
			if message == "" {
				message = "codexpool: unregistered gate profile or disallowed client"
			}
			c.AbortWithStatusJSON(http.StatusForbidden, gin.H{"error": message})
			return
		}
		if profile.extra != nil {
			profile.extra(c)
			if c.IsAborted() {
				return
			}
		}
		c.Next()
	}
}

// codexpoolConfigureEngine is passed to api.WithEngineConfigurator in cmd/server/main.go.
func codexpoolConfigureEngine(e *gin.Engine) { e.Use(codexpoolGate()) }

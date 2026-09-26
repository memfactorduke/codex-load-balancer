// codexpool_gate.go is added to CLIProxyAPI's cmd/server by `codexpool build`.
// It is the only change codexpool makes to upstream: one middleware installed through the engine
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
package main

import (
	"net"
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"
	log "github.com/sirupsen/logrus"
)

const codexpoolAppOrigin = "app://-"

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
		fromApp := origin == codexpoolAppOrigin
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
		c.Next()
	}
}

// codexpoolConfigureEngine is passed to api.WithEngineConfigurator in cmd/server/main.go.
func codexpoolConfigureEngine(e *gin.Engine) { e.Use(codexpoolGate()) }

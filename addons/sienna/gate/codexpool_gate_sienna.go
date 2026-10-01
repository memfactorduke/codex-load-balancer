// Added alongside the core gate only for builds carrying the Sienna add-on.
package main

import (
	"net/http"
	"path"
	"strings"
	"sync"

	"github.com/gin-gonic/gin"
	"github.com/router-for-me/CLIProxyAPI/v7/internal/runtime/executor/helps"
	log "github.com/sirupsen/logrus"
)

// Log at most one identity per supported entrypoint, not each caller-selected UA.
// This bounds both memory and first-client log volume for the process lifetime.
var codexpoolSeenClients sync.Map

func codexpoolClientClass(ua string) string {
	if strings.Contains(ua, "claude-desktop-3p") {
		return "desktop-3p"
	}
	return "cli"
}

// codexpoolManagementPath: the path gin routes on (it does not clean paths) is the management API's, with no
// "..", "." or doubled slash that could make it name another route.
func codexpoolManagementPath(p string) bool {
	return (p == "/v0/management" || strings.HasPrefix(p, "/v0/management/")) && path.Clean(p) == p
}

func init() {
	codexpoolRegisterProfile("claude", codexpoolGateProfile{
		allowed: func(r *http.Request) bool {
			return codexpoolManagementPath(r.URL.Path) ||
				(strings.HasPrefix(r.Header.Get("User-Agent"), "claude-cli/") && r.Header.Get("X-App") == "cli")
		},
		rejection: "codexpool: only foreground Claude Code may use this pool; start background sessions with CLAUDEPOOL=off",
		extra:     codexpoolSiennaGate,
	})
}

func codexpoolSiennaGate(c *gin.Context) {
	r := c.Request
	// Unlike Messages, CPA's token counter has no direct passthrough fallback.
	// Its native detector needs headers only for this endpoint. Reject rather
	// than let an unrecognised client acquire CPA's reconstructed identity.
	if r.URL.Path == "/v1/messages/count_tokens" &&
		!helps.DetectClaudeCodeRequest(r.Header, nil, true).Confirmed {
		log.Warnf("codexpool gate: rejected unrecognised token-count client user-agent=%q x-app=%q",
			r.Header.Get("User-Agent"), r.Header.Get("X-App"))
		c.AbortWithStatusJSON(http.StatusForbidden, gin.H{
			"error": "codexpool: this Claude Code token-count client is not recognised; a newer Claude Code release may be newer than this pool knows; update codexpool or run direct",
		})
		return
	}
	if !codexpoolManagementPath(r.URL.Path) {
		if _, seen := codexpoolSeenClients.LoadOrStore(codexpoolClientClass(r.UserAgent()), true); !seen {
			log.Infof("codexpool gate: first request from client user-agent=%q x-app=%q", r.UserAgent(), r.Header.Get("X-App"))
		}
	}
}

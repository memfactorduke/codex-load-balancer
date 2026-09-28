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

// The default profile is tested without any add-on or upstream detector.
func TestCodexpoolGateDefault(t *testing.T) {
	old := codexpoolProfile
	t.Cleanup(func() { codexpoolProfile = old })
	for _, tc := range []struct {
		name, profile, host, origin, site, route string
		want                                     int
	}{
		{name: "local", host: "127.0.0.1:8319", want: 204},
		{name: "localhost", host: "LOCALHOST:8319", want: 204},
		{name: "ipv6", host: "[::1]:8319", want: 204},
		{name: "rebind", host: "attacker.invalid", want: 403},
		{name: "browser", origin: "https://example.invalid", want: 403},
		{name: "null origin", origin: "null", want: 403},
		{name: "cross site", site: "cross-site", want: 403},
		{name: "same site", site: "same-origin", want: 403},
		{name: "navigation", site: "none", want: 204},
		{name: "renderer", origin: "app://-", site: "cross-site", want: 204},
		{name: "renderer rebind", host: "attacker.invalid", origin: "app://-", want: 403},
		{name: "unknown", profile: "unregistered-test-profile", want: 403},
		{name: "unknown renderer", profile: "unregistered-test-profile", origin: "app://-", want: 403},
		{name: "unknown management", profile: "unregistered-test-profile", route: "/v0/management/auth-files", want: 403},
	} {
		t.Run(tc.name, func(t *testing.T) {
			codexpoolProfile = tc.profile
			route := tc.route
			if route == "" {
				route = "/v1/responses"
			}
			body := `{"input":"unchanged"}`
			r := httptest.NewRequest(http.MethodPost, "http://127.0.0.1"+route, strings.NewReader(body))
			if tc.host != "" {
				r.Host = tc.host
			}
			r.Header.Set("Origin", tc.origin)
			r.Header.Set("Sec-Fetch-Site", tc.site)
			headers := r.Header.Clone()
			e := gin.New()
			codexpoolConfigureEngine(e)
			e.NoRoute(func(c *gin.Context) {
				got, err := io.ReadAll(c.Request.Body)
				if err != nil || string(got) != body || !reflect.DeepEqual(headers, c.Request.Header) {
					t.Fatal("gate changed request")
				}
				c.Status(http.StatusNoContent)
			})
			w := httptest.NewRecorder()
			e.ServeHTTP(w, r)
			if w.Code != tc.want {
				t.Fatalf("got %d, want %d", w.Code, tc.want)
			}
		})
	}
}

func TestCodexpoolGateRegistration(t *testing.T) {
	for _, name := range []string{"", " spaced ", "missing-handler"} {
		t.Run(name, func(t *testing.T) {
			defer func() {
				if recover() == nil {
					t.Fatal("invalid registration accepted")
				}
			}()
			codexpoolRegisterProfile(name, codexpoolGateProfile{})
		})
	}
	name := "test-profile"
	old := codexpoolProfile
	t.Cleanup(func() { delete(codexpoolProfiles, name); codexpoolProfile = old })
	codexpoolRegisterProfile(name, codexpoolGateProfile{
		allowed: func(*http.Request) bool { return true },
		extra:   func(c *gin.Context) { c.AbortWithStatus(http.StatusForbidden) },
	})
	codexpoolProfile = name
	e := gin.New()
	codexpoolConfigureEngine(e)
	e.GET("/", func(c *gin.Context) { t.Fatal("aborted profile reached handler") })
	w := httptest.NewRecorder()
	e.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "http://127.0.0.1/", nil))
	if w.Code != 403 {
		t.Fatalf("got %d, want 403", w.Code)
	}
	defer func() {
		if recover() == nil {
			t.Fatal("duplicate registration accepted")
		}
	}()
	codexpoolRegisterProfile(name, codexpoolProfiles[name])
}

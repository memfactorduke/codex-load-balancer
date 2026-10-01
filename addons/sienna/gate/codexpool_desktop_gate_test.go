package main

import (
	"encoding/json"
	"io"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

// The same real captured inputs used by the executor must satisfy the gate.
func TestCodexpoolDesktopWireGateFixtures(t *testing.T) {
	oldProfile := codexpoolProfile
	t.Cleanup(func() { codexpoolProfile = oldProfile })
	codexpoolProfile = "claude"
	for _, name := range []string{"desktop-initial.json", "desktop-retry.json", "cli.json"} {
		t.Run(name, func(t *testing.T) {
			raw, err := os.ReadFile(filepath.Join("..", "..", "internal", "runtime", "executor", "testdata", "codexpool-desktop", name))
			if os.IsNotExist(err) {
				t.Fatal("desktop wire fixtures absent")
			}
			if err != nil {
				t.Fatal(err)
			}
			var fixture struct {
				Headers map[string]string `json:"headers"`
				Body    json.RawMessage   `json:"body"`
			}
			if json.Unmarshal(raw, &fixture) != nil {
				t.Fatal("invalid desktop fixture")
			}
			route, want := "/v1/messages", 204
			request := httptest.NewRequest("POST", "http://127.0.0.1"+route, strings.NewReader(string(fixture.Body)))
			for k, v := range fixture.Headers {
				request.Header.Set(k, v)
			}
			before := request.Header.Clone()
			router := gin.New()
			codexpoolConfigureEngine(router)
			router.POST(route, func(c *gin.Context) {
				body, _ := io.ReadAll(c.Request.Body)
				if string(body) != string(fixture.Body) || !reflect.DeepEqual(before, c.Request.Header) {
					t.Error("gate changed desktop request")
				}
				c.Status(204)
			})
			response := httptest.NewRecorder()
			router.ServeHTTP(response, request)
			if response.Code != want {
				t.Errorf("status %d, expected %d", response.Code, want)
			}
		})
	}
}

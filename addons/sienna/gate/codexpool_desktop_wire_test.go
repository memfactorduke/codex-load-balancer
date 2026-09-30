// Copied into CPA's executor package by codexpool build. No sockets or real credentials.
package executor

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/router-for-me/CLIProxyAPI/v7/internal/config"
	"github.com/router-for-me/CLIProxyAPI/v7/internal/runtime/executor/helps"
	authpkg "github.com/router-for-me/CLIProxyAPI/v7/sdk/cliproxy/auth"
	execpkg "github.com/router-for-me/CLIProxyAPI/v7/sdk/cliproxy/executor"
	translator "github.com/router-for-me/CLIProxyAPI/v7/sdk/translator"
)

type desktopWireFixture struct {
	Capture     map[string]interface{} `json:"capture"`
	Synthesised bool                   `json:"synthesised"`
	Headers     map[string]string      `json:"headers"`
	Body        map[string]interface{} `json:"body"`
}
type desktopWireTransport func(*http.Request) (*http.Response, error)

func (f desktopWireTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

// Report paths only: even a subsequently captured fixture must not leak values.
func desktopWireDiff(a, b interface{}, path string) []string {
	if reflect.DeepEqual(a, b) {
		return nil
	}
	am, aok := a.(map[string]interface{})
	bm, bok := b.(map[string]interface{})
	if aok && bok {
		keys := map[string]bool{}
		for k := range am {
			keys[k] = true
		}
		for k := range bm {
			keys[k] = true
		}
		var out []string
		for k := range keys {
			av, ap := am[k]
			bv, bp := bm[k]
			if !ap || !bp {
				out = append(out, path+"."+k)
			} else {
				out = append(out, desktopWireDiff(av, bv, path+"."+k)...)
			}
		}
		sort.Strings(out)
		return out
	}
	aa, aok := a.([]interface{})
	ba, bok := b.([]interface{})
	if aok && bok && len(aa) == len(ba) {
		var out []string
		for i := range aa {
			out = append(out, desktopWireDiff(aa[i], ba[i], fmt.Sprintf("%s[%d]", path, i))...)
		}
		return out
	}
	return []string{path}
}

func desktopWireSessionMatches(body map[string]interface{}, headers http.Header, expected string) bool {
	metadata, ok := body["metadata"].(map[string]interface{})
	if !ok {
		return false
	}
	userID, ok := metadata["user_id"].(string)
	if !ok {
		return false
	}
	var identity struct {
		SessionID string `json:"session_id"`
	}
	return json.Unmarshal([]byte(userID), &identity) == nil && expected != "" &&
		identity.SessionID == expected && headers.Get("X-Claude-Code-Session-Id") == expected
}

func TestCodexpoolDesktopWireSessionConsistency(t *testing.T) {
	for _, test := range []struct {
		name, header, userID, expected string
		ok                             bool
	}{
		{"matching", "derived", `{"session_id":"derived"}`, "derived", true},
		{"header differs", "caller", `{"session_id":"derived"}`, "derived", false},
		{"body differs", "derived", `{"session_id":"caller"}`, "derived", false},
		{"both wrong", "caller", `{"session_id":"caller"}`, "derived", false},
		{"missing header", "", `{"session_id":"derived"}`, "derived", false},
		{"missing session", "derived", `{}`, "derived", false},
		{"invalid identity", "derived", `invalid`, "derived", false},
		{"empty", "", `{}`, "", false},
	} {
		t.Run(test.name, func(t *testing.T) {
			headers := http.Header{}
			headers.Set("X-Claude-Code-Session-Id", test.header)
			body := map[string]interface{}{"metadata": map[string]interface{}{"user_id": test.userID}}
			if desktopWireSessionMatches(body, headers, test.expected) != test.ok {
				t.Fatal("session consistency check returned the wrong result")
			}
		})
	}
}

func desktopWireCanonical(h http.Header) http.Header {
	out := http.Header{}
	for k, values := range h {
		for _, value := range values {
			out.Add(k, value)
		}
	}
	return out
}

func desktopWireCompare(t *testing.T, inbound, outbound []byte, sent, got http.Header, auth *authpkg.Auth) {
	t.Helper()
	var a, b map[string]interface{}
	if json.Unmarshal(inbound, &a) != nil || json.Unmarshal(outbound, &b) != nil {
		t.Fatal("invalid JSON body")
	}
	// A native desktop caller owns its session; do not derive an expected ID
	// through CPA's implementation, which could hide a detection regression.
	session := sent.Get("X-Claude-Code-Session-Id")
	if !desktopWireSessionMatches(a, sent, session) {
		t.Fatal("fixture must have matching caller session IDs in header and body")
	}
	// Independently construct the identity expectation. Do not use CPA's rewriter
	// as its own oracle; account/device are fixed fake serving-credential values.
	identity := func(obj map[string]interface{}) map[string]interface{} {
		metadata, ok := obj["metadata"].(map[string]interface{})
		if !ok {
			t.Fatal("$.metadata missing or invalid")
		}
		userID, ok := metadata["user_id"].(string)
		var result map[string]interface{}
		if !ok || json.Unmarshal([]byte(userID), &result) != nil {
			t.Fatal("$.metadata.user_id invalid")
		}
		return result
	}
	wantIdentity, gotIdentity := identity(a), identity(b)
	wantIdentity["account_uuid"] = auth.Metadata["account_uuid"]
	wantIdentity["device_id"] = strings.Repeat("0", 64)
	for _, path := range desktopWireDiff(wantIdentity, gotIdentity, "$.metadata.user_id") {
		t.Error(path)
	}
	// Credential rebinding may change device/account fields, never the caller session.
	if !desktopWireSessionMatches(b, got, session) {
		t.Error("$.headers.X-Claude-Code-Session-Id and $.metadata.user_id.session_id must equal the caller session")
	}
	a["metadata"].(map[string]interface{})["user_id"] = b["metadata"].(map[string]interface{})["user_id"]
	signed, err := signAnthropicMessagesBody(outbound)
	if err != nil || !bytes.Equal(signed, outbound) {
		t.Error("$.system[0].text.cch checksum")
	}
	cch := regexp.MustCompile(` cch=[0-9a-f]{5};`)
	for _, obj := range []map[string]interface{}{a, b} {
		if system, ok := obj["system"].([]interface{}); ok && len(system) > 0 {
			if first, ok := system[0].(map[string]interface{}); ok {
				if text, ok := first["text"].(string); ok {
					first["text"] = cch.ReplaceAllString(text, "")
				}
			}
		}
	}
	for _, path := range desktopWireDiff(a, b, "$") {
		t.Error(path)
	}
	got = desktopWireCanonical(got)
	want := desktopWireCanonical(sent)
	want.Set("Authorization", "Bearer sk-ant-oat-desktop-wire")
	betas := strings.Split(want.Get("Anthropic-Beta"), ",")
	var expectedBetas []string
	hasOAuth := false
	for _, beta := range betas {
		hasOAuth = hasOAuth || beta == "oauth-2025-04-20"
	}
	for _, beta := range betas {
		expectedBetas = append(expectedBetas, beta)
		if beta == "claude-code-20250219" && !hasOAuth {
			expectedBetas = append(expectedBetas, "oauth-2025-04-20")
		}
	}
	want.Set("Anthropic-Beta", strings.Join(expectedBetas, ","))
	var policy struct {
		Framing []string `json:"framing_header_exclusions"`
	}
	policyBytes, err := os.ReadFile("testdata/codexpool-desktop/allowed-differences.json")
	if err != nil || json.Unmarshal(policyBytes, &policy) != nil || len(policy.Framing) != 9 {
		t.Fatal("allowed-difference policy missing or invalid")
	}
	transport := map[string]bool{}
	for _, name := range policy.Framing {
		transport[name] = true
	}

	keys := map[string]bool{}
	for k := range want {
		keys[k] = true
	}
	for k := range got {
		keys[k] = true
	}
	for k := range keys {
		if !transport[k] && !reflect.DeepEqual(want.Values(k), got.Values(k)) {
			t.Error("$.headers." + k)
		}
	}
}

func TestCodexpoolDesktopWire(t *testing.T) {
	files, err := filepath.Glob("testdata/codexpool-desktop/*.json")
	if err != nil {
		t.Fatal(err)
	}
	if len(files) != 4 {
		t.Fatal("codexpool desktop fixtures absent or incomplete")
	}
	for _, file := range files {
		if filepath.Base(file) == "allowed-differences.json" {
			continue
		} // comparison policy, not a request
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%v", filepath.Base(file), stream), func(t *testing.T) {
				raw, err := os.ReadFile(file)
				if err != nil {
					t.Fatal(err)
				}
				var fixture desktopWireFixture
				if json.Unmarshal(raw, &fixture) != nil {
					t.Fatal("invalid fixture")
				}
				if fixture.Synthesised || fixture.Capture["kind"] != "real-binary-loopback" {
					t.Fatal("real capture provenance required")
				}
				// Captures are streaming. stream=false is explicitly derived executor coverage.
				if !stream {
					t.Log("DERIVED: captured body with stream=false for Execute coverage")
				}
				// Execute and ExecuteStream define transport; keep the same engine content for both.
				fixture.Body["stream"] = stream
				payload, _ := json.Marshal(fixture.Body)
				headers := http.Header{}
				for k, v := range fixture.Headers {
					headers.Set(k, v)
				}
				var captured []byte
				var capturedHeaders http.Header
				rt := desktopWireTransport(func(r *http.Request) (*http.Response, error) {
					if r.URL.Host != "api.anthropic.com" || r.URL.Path != "/v1/messages" {
						return nil, fmt.Errorf("unexpected upstream route")
					}
					captured, _ = io.ReadAll(r.Body)
					capturedHeaders = r.Header.Clone()
					contentType, body := "application/json", `{"id":"msg_wire","type":"message","role":"assistant","model":"claude-opus-5-5","content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1}}`
					if stream {
						contentType = "text/event-stream"
						body = "event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_wire\",\"type\":\"message\",\"role\":\"assistant\",\"model\":\"claude-opus-5-5\",\"content\":[],\"usage\":{\"input_tokens\":1,\"output_tokens\":0}}}\n\nevent: message_stop\ndata: {\"type\":\"message_stop\"}\n\n"
					}
					return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": {contentType}}, Body: io.NopCloser(strings.NewReader(body)), Request: r}, nil
				})
				ctx := context.WithValue(context.Background(), "cliproxy.roundtripper", http.RoundTripper(rt))
				gin.SetMode(gin.TestMode)
				gc, _ := gin.CreateTestContext(httptest.NewRecorder())
				gc.Request = httptest.NewRequest("POST", "http://127.0.0.1"+fixture.Capture["path"].(string), bytes.NewReader(payload))
				gc.Request.Header = headers.Clone()
				ctx = context.WithValue(ctx, "gin", gc)
				auth := &authpkg.Auth{ID: "desktop-wire", Attributes: map[string]string{"api_key": "sk-ant-oat-desktop-wire", "base_url": "https://api.anthropic.com"}, Metadata: map[string]interface{}{"account_uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "claude_device_ids": []string{strings.Repeat("0", 64)}}}
				executor := NewClaudeExecutor(&config.Config{DisableClaudeCloakMode: true})
				req := execpkg.Request{Model: fixture.Body["model"].(string), Payload: payload}
				opts := execpkg.Options{SourceFormat: translator.FormatClaude, Headers: headers, OriginalRequest: payload}
				if stream {
					var result *execpkg.StreamResult
					result, err = executor.ExecuteStream(ctx, auth, req, opts)
					if err == nil {
						for chunk := range result.Chunks {
							if chunk.Err != nil {
								t.Fatal("stream failed")
							}
						}
					}
				} else {
					_, err = executor.Execute(ctx, auth, req, opts)
				}
				if err != nil {
					t.Fatal("executor failed before comparison")
				}
				if captured == nil {
					t.Fatal("upstream not called")
				}
				capturedHeaders = desktopWireCanonical(capturedHeaders)
				var before, after map[string]interface{}
				_ = json.Unmarshal(payload, &before)
				_ = json.Unmarshal(captured, &after)
				detection := helps.DetectClaudeCodeRequest(headers, payload, false)
				t.Logf("DETECTION confirmed=%v entrypoint=%q", detection.Confirmed, detection.Entrypoint)
				t.Logf("BODY_DIFF %v", desktopWireDiff(before, after, "$"))
				t.Logf("BILLING_BEFORE %s", before["system"].([]interface{})[0].(map[string]interface{})["text"])
				t.Logf("BILLING_AFTER %s", after["system"].([]interface{})[0].(map[string]interface{})["text"])
				headerKeys := map[string]bool{}
				for k := range headers {
					headerKeys[k] = true
				}
				for k := range capturedHeaders {
					headerKeys[k] = true
				}
				changed := []string{}
				for k := range headerKeys {
					if !reflect.DeepEqual(headers.Values(k), capturedHeaders.Values(k)) {
						changed = append(changed, k)
					}
				}
				sort.Strings(changed)
				t.Logf("HEADER_DIFF %v", changed)
				for _, key := range []string{"User-Agent", "X-Claude-Code-Session-Id", "Accept-Encoding", "X-Client-Request-Id"} {
					t.Logf("VALUE %s before=%q after=%q", key, headers.Get(key), capturedHeaders.Get(key))
				}
				t.Logf("BETA_BEFORE %s", headers.Get("Anthropic-Beta"))
				t.Logf("BETA_AFTER %s", capturedHeaders.Get("Anthropic-Beta"))
				desktopWireCompare(t, payload, captured, headers, capturedHeaders, auth)
			})
		}
	}
}

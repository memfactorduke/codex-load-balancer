package handlers

import (
    "encoding/json"
    "reflect"
    "regexp"
    "testing"
)

// Spec §10 rows 4 and 6: CPA treats nonnumeric codes as 502 internally but
// must preserve the bridge error verbatim for Codex's stream classifier.
func TestClaudeLaneFailedChunk(t *testing.T) {
    for _, code := range []string{"engine_error", "engine_exited", "rate_limit_exceeded"} {
        for _, nested := range []bool{false, true} {
            detail := map[string]any{"type": code, "code": code, "message": "Claude Code error."}
            if code == "rate_limit_exceeded" {
                detail["message"] = "Claude rate limit. Try again in 17.5 seconds."
                detail["resets_at"] = float64(1234567890)
            }
            payload := map[string]any{"error": detail}
            if nested { payload = map[string]any{"response": payload} }
            raw, err := json.Marshal(payload)
            if err != nil { t.Fatal(err) }
            chunk := BuildOpenAIResponsesStreamFailedChunk(502, string(raw), 7)
            var got map[string]any
            if err := json.Unmarshal(chunk, &got); err != nil { t.Fatal(err) }
            response, ok := got["response"].(map[string]any)
            if !ok { t.Fatalf("missing response: %s", chunk) }
            if got["type"] != "response.failed" || response["status"] != "failed" || got["sequence_number"] != float64(7) {
                t.Fatalf("bad envelope: %s", chunk)
            }
            if !reflect.DeepEqual(response["error"], detail) { t.Fatalf("error changed: %s", chunk) }
            if _, ok := response["error"].(map[string]any)["code"].(string); !ok { t.Fatal("code must remain a string") }
            if code == "rate_limit_exceeded" && !regexp.MustCompile(`(?i)try again in\s*(\d+(?:\.\d+)?)\s*(s|ms|seconds?)`).MatchString(detail["message"].(string)) {
                t.Fatal("Codex cannot parse retry delay")
            }
        }
    }
}

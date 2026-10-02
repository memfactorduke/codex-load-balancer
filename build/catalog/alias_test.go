package models

import (
	"reflect"
	"testing"

	"github.com/router-for-me/CLIProxyAPI/v7/internal/registry"
)

func TestCodexpoolCatalogAliasProjection(t *testing.T) {
	const alias = "astra-max-speed"
	const base = "gpt-6-astra"
	const fallback = "codexpool-test-alias-without-template"
	r := registry.GetGlobalRegistry()
	providers := func(string) []string { return []string{"codex"} }
	project := func(id string) map[string]any {
		entries := BuildResponse([]map[string]any{{"id": id}}, providers, false)["models"].([]map[string]any)
		if len(entries) != 1 {
			t.Fatalf("projection count = %d", len(entries))
		}
		return entries[0]
	}
	ordinaryBefore := project(base)
	r.RegisterClient("codexpool-test-speed-seat", "codex", []*registry.ModelInfo{
		{ID: alias, MetadataModelID: base, Type: "codex"},
		{ID: fallback, MetadataModelID: base, Type: "codex"},
	})
	t.Cleanup(func() { r.UnregisterClient("codexpool-test-speed-seat") })
	speed := project(alias)
	if speed["display_name"] != "Astra Max Speed" || speed["default_service_tier"] != "ultrafast" {
		t.Fatalf("alias did not select its exact catalog template: name=%v tier=%v", speed["display_name"], speed["default_service_tier"])
	}
	tiers, ok := speed["service_tiers"].([]any)
	if !ok || len(tiers) != 1 || tiers[0].(map[string]any)["id"] != "ultrafast" {
		t.Fatalf("alias service tiers = %#v", speed["service_tiers"])
	}
	if !reflect.DeepEqual(speed["supported_reasoning_levels"], ordinaryBefore["supported_reasoning_levels"]) {
		t.Fatal("speed alias changed reasoning choices")
	}
	if got := codexClientMetadataModelID(alias); got != base {
		t.Fatalf("base metadata changed: %q", got)
	}
	ordinaryAfter := project(base)
	if !reflect.DeepEqual(ordinaryBefore, ordinaryAfter) {
		t.Fatal("ordinary model metadata changed")
	}
	inherited := project(fallback)
	inherited["slug"] = base
	if !reflect.DeepEqual(inherited, ordinaryBefore) {
		t.Fatal("alias without exact template lost base fallback")
	}
	// Native aliases inherit their base description; an exact alias template
	// must retain its speed/usage explanation while accepting the display name.
	for _, id := range []string{alias, base, fallback} {
		provided := map[string]any{"id": id, "description": "Description from base registration", "display_name": "Astra Max Speed"}
		entry := BuildResponse([]map[string]any{provided}, providers, false)["models"].([]map[string]any)[0]
		wantDescription := provided["description"]
		if id == alias {
			wantDescription = "Ultrafast speed; uses 8× Standard included usage. Reasoning effort is separate."
		}
		if entry["description"] != wantDescription || entry["display_name"] != provided["display_name"] {
			t.Fatalf("description/display projection for %s = %v / %v", id, entry["description"], entry["display_name"])
		}
	}
}

func TestCodexpoolCatalogAliasDoesNotRegisterSeats(t *testing.T) {
	const alias = "astra-max-speed"
	r := registry.GetGlobalRegistry()
	r.RegisterClient("codexpool-test-unrelated-seat", "codex", []*registry.ModelInfo{
		{ID: "gpt-6-astra", Type: "codex"},
	})
	t.Cleanup(func() { r.UnregisterClient("codexpool-test-unrelated-seat") })
	if registry.LookupModelInfo(alias) != nil {
		t.Fatal("catalog metadata registered alias globally")
	}
	for _, m := range r.GetModelsForClient("codexpool-test-unrelated-seat") {
		if m.ID == alias {
			t.Fatal("catalog metadata registered alias on unrelated seat")
		}
	}
	entries := BuildResponse(r.GetAvailableModels("openai"), nil, false)["models"].([]map[string]any)
	for _, m := range entries {
		if m["slug"] == alias {
			t.Fatal("unavailable alias leaked into picker")
		}
	}
}

package fixturework

import "testing"

func TestRunIsDeterministicAndModeSensitive(t *testing.T) {
	first := Run(2, 8192)
	if second := Run(2, 8192); second != first {
		t.Fatalf("same workload changed: %#x != %#x", second, first)
	}
	if other := Run(3, 8192); other == first {
		t.Fatalf("distinct workload modes collided: %#x", other)
	}
}

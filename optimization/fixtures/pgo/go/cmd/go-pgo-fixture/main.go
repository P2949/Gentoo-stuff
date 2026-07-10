package main

import (
	"flag"
	"fmt"
	"os"
	"runtime/pprof"

	"example.com/gentoo-optimization/go-pgo-fixture/internal/fixturework"
)

func main() {
	mode := flag.Uint64("mode", 1, "workload mode")
	iterations := flag.Int("iterations", 50_000_000, "positive workload iteration count")
	cpuProfile := flag.String("cpuprofile", "", "write a Go CPU pprof profile")
	flag.Parse()
	if flag.NArg() != 0 || *mode == 0 || *iterations <= 0 {
		fmt.Fprintln(os.Stderr, "mode and iterations must be positive and no positional arguments are accepted")
		os.Exit(2)
	}

	var profileFile *os.File
	if *cpuProfile != "" {
		var err error
		profileFile, err = os.Create(*cpuProfile)
		if err != nil {
			fmt.Fprintf(os.Stderr, "create CPU profile: %v\n", err)
			os.Exit(1)
		}
		if err := pprof.StartCPUProfile(profileFile); err != nil {
			fmt.Fprintf(os.Stderr, "start CPU profile: %v\n", err)
			_ = profileFile.Close()
			os.Exit(1)
		}
	}

	checksum := fixturework.Run(*mode, *iterations)
	if profileFile != nil {
		pprof.StopCPUProfile()
		if err := profileFile.Close(); err != nil {
			fmt.Fprintf(os.Stderr, "close CPU profile: %v\n", err)
			os.Exit(1)
		}
	}
	fmt.Printf("mode=%d iterations=%d checksum=%016x\n", *mode, *iterations, checksum)
}

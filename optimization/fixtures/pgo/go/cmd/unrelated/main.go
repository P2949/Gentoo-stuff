package main

import (
	"flag"
	"fmt"
	"math/bits"
	"os"
	"runtime/pprof"
)

//go:noinline
func unrelatedMix(value uint64, iterations int) uint64 {
	for index := 0; index < iterations; index++ {
		value = bits.RotateLeft64(value+uint64(index), index&31+1)
		value ^= 0xd1b54a32d192ed03
	}
	return value
}

func main() {
	profilePath := flag.String("cpuprofile", "", "required output profile")
	iterations := flag.Int("iterations", 50_000_000, "workload iterations")
	flag.Parse()
	if *profilePath == "" || *iterations <= 0 {
		fmt.Fprintln(os.Stderr, "cpuprofile is required and iterations must be positive")
		os.Exit(2)
	}
	profileFile, err := os.Create(*profilePath)
	if err != nil {
		panic(err)
	}
	if err := pprof.StartCPUProfile(profileFile); err != nil {
		panic(err)
	}
	checksum := unrelatedMix(0x554e52454c415445, *iterations)
	pprof.StopCPUProfile()
	if err := profileFile.Close(); err != nil {
		panic(err)
	}
	fmt.Printf("unrelated checksum=%016x\n", checksum)
}

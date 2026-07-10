package fixturework

import "math/bits"

// HotEven and HotOdd deliberately retain distinct control flow so the CPU
// profile contains useful inlining, call-edge, and source-line information.
// They are exported only to make the fixture's profile validation unambiguous.
//
//go:noinline
func HotEven(value uint64, rounds int) uint64 {
	for index := 0; index < rounds; index++ {
		value = inlineRotate(value^uint64(index), uint(index&31)+1)
		if value&7 == 0 {
			value += 0xa0761d6478bd642f
		}
	}
	return value
}

//go:noinline
func HotOdd(value uint64, rounds int) uint64 {
	for index := 0; index < rounds; index++ {
		value = inlineRotate(value+uint64(index)^0xe7037ed1a0b428db, uint(index&15)+7)
		if value&15 == 3 {
			value ^= 0x8ebc6af09c88c6e3
		}
	}
	return value
}

func inlineRotate(value uint64, shift uint) uint64 {
	return bits.RotateLeft64(value, int(shift)) ^ value*0x9e3779b97f4a7c15
}

func Run(mode uint64, iterations int) uint64 {
	result := uint64(0x474f50474f303031) ^ mode*0xd6e8feb86659fd93
	rounds := iterations/16 + 1
	for index := 0; index < 16; index++ {
		if (mode+uint64(index))&1 == 0 {
			result = HotEven(result^uint64(index), rounds)
		} else {
			result = HotOdd(result+uint64(index), rounds)
		}
	}
	return result
}

/* A12 SoC image: one 128 KB dual-port BRAM at 0x80000000 holding both the
   program and its data, which is what lets the identical ELF run on Spike, in
   the A5 cosimulation and in the bitstream.  The stack starts at the top of the
   array and grows down toward .bss; there is no guard page and nothing to trap
   an overflow, so keep an eye on it when A13 brings CoreMark's working set. */
OUTPUT_ARCH("riscv")
ENTRY(_start)

MEMORY {
  RAM (rwx) : ORIGIN = 0x80000000, LENGTH = 128K
}

SECTIONS {
  .text.init : { *(.text.init) } > RAM
  .text      : { *(.text) *(.text.*) } > RAM
  .rodata    : { *(.rodata) *(.rodata.*) *(.srodata) *(.srodata.*) } > RAM
  .data      : { *(.data) *(.data.*) *(.sdata) *(.sdata.*) } > RAM
  .bss       : {
    __bss_start = .;
    *(.bss) *(.bss.*) *(.sbss) *(.sbss.*) *(COMMON)
    __bss_end = .;
  } > RAM
  _end = .;

  _stack_top = ORIGIN(RAM) + LENGTH(RAM);
}

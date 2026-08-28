/* Minimal bare-metal link script for Spike (HTIF at DRAM base 0x80000000). */
OUTPUT_ARCH("riscv")
ENTRY(_start)
SECTIONS {
  . = 0x80000000;
  .text.init : { *(.text.init) }
  .text      : { *(.text) *(.text.*) }
  .rodata    : { *(.rodata) *(.rodata.*) }
  .data      : { *(.data) *(.data.*) }
  /* Spike finds the HTIF console/exit mailbox by these symbol names. */
  .tohost ALIGN(0x1000) : { *(.tohost) }
  .bss       : { *(.bss) *(.bss.*) *(COMMON) }
  _end = .;
}

/* Bare-metal link script for the ML-KEM KAT runs on Spike.
 * Same shape as sw/tests/link.ld, plus a stack and heap-free BSS. */
OUTPUT_ARCH("riscv")
ENTRY(_start)
SECTIONS {
  . = 0x80000000;
  .text.init : { *(.text.init) }
  .text      : { *(.text) *(.text.*) }
  .rodata    : { *(.rodata) *(.rodata.*) *(.srodata) *(.srodata.*) }
  .data      : { *(.data) *(.data.*) *(.sdata) *(.sdata.*) }
  /* Spike's fesvr finds the HTIF mailbox by these symbol names. */
  .tohost ALIGN(0x1000) : { *(.tohost) }
  .bss ALIGN(0x10) : {
    __bss_start = .;
    *(.bss) *(.bss.*) *(.sbss) *(.sbss.*) *(COMMON)
    __bss_end = .;
  }
  . = ALIGN(16);
  _end = .;
  . = . + 0x40000;          /* 256 KiB stack: ML-KEM-768 keygen is stack-hungry */
  _stack_top = .;
}

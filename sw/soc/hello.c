/*
 * The SoC bring-up program.  Its UART output is designed to be parsed
 * (tb/fpga/parse_soc_uart.py): fixed banner, key=value lines, fixed
 * terminator, repeating forever with a delay between blocks, so a capture
 * started at any moment catches a whole block and `iter` distinguishes a
 * stale capture from a live one.
 *
 *     === rvntt soc ===
 *     hello=Hello, world!
 *     sw=0xN btn=0xN
 *     mcycle=0xXXXXXXXX minstret=0xXXXXXXXX
 *     echo=0xNN            (or echo=none)
 *     img=OK               (or img=BAD)
 *     iter=0xXXXXXXXX
 *     === end ===
 *
 * `echo` returns whatever byte the host sent since the last block, proving
 * the receive path.  `img` re-reads .text.init and compares it against the
 * value taken before the first store: rvntt_ram drops the high address bits,
 * so an MMIO store not gated out of the RAM would land on word 0 (crt0) and
 * nothing else would notice.
 */

#define MMIO_BASE   0x40000000u
#define UART_TX     (*(volatile unsigned int *)(MMIO_BASE + 0x00))
#define UART_STAT   (*(volatile unsigned int *)(MMIO_BASE + 0x04))
#define UART_RX     (*(volatile unsigned int *)(MMIO_BASE + 0x08))
#define GPIO_OUT    (*(volatile unsigned int *)(MMIO_BASE + 0x0C))
#define GPIO_IN     (*(volatile unsigned int *)(MMIO_BASE + 0x10))

#define STAT_TX_READY   0x1u
#define STAT_RX_VALID   0x2u
#define STAT_RX_OVERRUN 0x4u

/* Cycles between blocks.  Overridden to something tiny for the Verilator run;
 * one binary, two build-time constants, so the simulated program and the
 * programmed one are the same source. */
#ifndef DELAY_CYCLES
#define DELAY_CYCLES 75000000u
#endif

/* Words of the image to verify: 96 words = 384 bytes from the reset vector,
 * which covers crt0 and the head of .text -- and, more to the point, every word
 * an MMIO store could alias onto (the map only reaches offset 0x10, i.e. word
 * 4).  Rotate-then-add so the sum is order sensitive. */
#define IMG_WORDS 96

static unsigned int img_sum(void)
{
    const volatile unsigned int *p = (const volatile unsigned int *)0x80000000u;
    unsigned int acc = 0;
    int i;
    for (i = 0; i < IMG_WORDS; i++) {
        acc = (acc << 1) | (acc >> 31);
        acc += p[i];
    }
    return acc;
}

static inline unsigned int rd_mcycle(void)
{
    unsigned int v;
    __asm__ volatile ("csrr %0, mcycle" : "=r"(v));
    return v;
}

static inline unsigned int rd_minstret(void)
{
    unsigned int v;
    __asm__ volatile ("csrr %0, minstret" : "=r"(v));
    return v;
}

static void putc_(char c)
{
    while (!(UART_STAT & STAT_TX_READY))
        ;
    UART_TX = (unsigned char)c;
}

static void puts_(const char *s)
{
    while (*s)
        putc_(*s++);
}

static void puthex(unsigned int v, int digits)
{
    static const char hx[] = "0123456789ABCDEF";
    int i;
    putc_('0');
    putc_('x');
    for (i = digits - 1; i >= 0; i--)
        putc_(hx[(v >> (i * 4)) & 0xF]);
}

/* Busy-wait on mcycle rather than on a counted loop.  A counted loop's duration
 * depends on what the compiler does to it; mcycle is the architectural clock
 * and gives the same wall time whatever -O level this is built at. */
static void delay_cycles(unsigned int n)
{
    unsigned int t0 = rd_mcycle();
    while ((unsigned int)(rd_mcycle() - t0) < n)
        ;
}

int main(void)
{
    unsigned int iter = 0;
    /* Taken before ANY store to the MMIO region, so it is the pristine image. */
    unsigned int img0 = img_sum();

    for (;;) {
        unsigned int gin, cyc, ins, stat;

        /* Show progress on the four green LEDs before anything else: if the
         * UART is misconfigured, a binary count on the LEDs still proves the
         * CPU is running this program and not something else. */
        GPIO_OUT = iter & 0xF;

        gin  = GPIO_IN;
        cyc  = rd_mcycle();
        ins  = rd_minstret();

        puts_("=== rvntt soc ===\r\n");
        puts_("hello=Hello, world!\r\n");

        puts_("sw=");
        puthex(gin & 0xF, 1);
        puts_(" btn=");
        puthex((gin >> 4) & 0xF, 1);
        puts_("\r\n");

        puts_("mcycle=");
        puthex(cyc, 8);
        puts_(" minstret=");
        puthex(ins, 8);
        puts_("\r\n");

        stat = UART_STAT;
        puts_("echo=");
        if (stat & STAT_RX_VALID) {
            puthex(UART_RX & 0xFF, 2);
            UART_RX = 0;                 /* explicit consume; reads are pure */
        } else {
            puts_("none");
        }
        if (stat & STAT_RX_OVERRUN) {
            puts_(" overrun");
            UART_STAT = STAT_RX_OVERRUN; /* write-1-to-clear */
        }
        puts_("\r\n");

        {
            unsigned int img = img_sum();
            puts_("img=");
            if (img == img0) {
                puts_("OK");
            } else {
                puts_("BAD ");
                puthex(img, 8);
            }
            puts_("\r\n");
        }

        puts_("iter=");
        puthex(iter, 8);
        puts_("\r\n");
        puts_("=== end ===\r\n");

        iter++;
        delay_cycles(DELAY_CYCLES);
    }
}

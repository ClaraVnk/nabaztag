// SPDX-License-Identifier: GPL-2.0-only
/*
 * The Karotz (Nabaztag v3): machine RABBITV3, number 1912. A Samsung
 * S3C2440A on a 2440SODIMM module, 64 MB of SDRAM and 256 MB of NAND.
 *
 * LapinLibre's board file for Linux 2.6.32 (kernel/src/arch/arm/mach-s3c2440/
 * mach-rabbitv3.c, itself after mach-smdk2440.c), ported to Linux 6.1 after
 * mach-mini2440.c (Terrier OS, step 4 of docs/TERRIER-OS-KERNEL.md,
 * 2026-11-22). What changed, part by part:
 *
 * - GPIO: 6.1 has no raw GPxCON/GPxUP macros; the same pin functions and
 *   pull-ups are set pin by pin (s3c_gpio_cfgpin/setpull). Pins 2.6.32's
 *   whole-register writes left as inputs and that nothing here names are
 *   left as the bootloader set them.
 * - NAND: disable_ecc became engine_type NAND_ECC_ENGINE_TYPE_NONE.
 * - SPI: the LED driver's chip select (GPG2, chip select 1) is a GPIO
 *   lookup ("cs", index 1) instead of a set_cs callback; it is "karotz-leds",
 *   a name 6.1's spidev knows ("spidev" no longer binds).
 * - USB device: the D+ pull-up (GPG11) is a GPIO lookup ("pullup").
 * - USB host: UPLLCON written as 2.6.32 and the BIOS do (6.1's clock
 *   framework cannot set it), the controller put in reset before a restart.
 * - ramoops: 1 MiB at 0x32000000 reserved and registered by the board.
 * - SPI0's clock gets the lookup 6.1 does not give it, prepared for a
 *   driver that only enables it.
 * - I2C: bus_freq/max_freq became frequency.
 * - Left out: the LCD (the Karotz has no screen), the camera interface and
 *   the PWM devices, which steps 5 and 7 bring back with their drivers, and
 *   the ADC's two battery/power inputs (hwmon): 6.1's S3C_ADC depends on
 *   !ARCH_MULTIPLATFORM, which a 6.1 S3C24xx kernel is (step 7).
 */

#include <linux/kernel.h>
#include <linux/types.h>
#include <linux/init.h>
#include <linux/clk.h>
#include <linux/clk-provider.h>
#include <linux/clkdev.h>
#include <linux/gpio.h>
#include <linux/gpio/machine.h>
#include <linux/gpio_keys.h>
#include <linux/input.h>
#include <linux/io.h>
#include <linux/delay.h>
#include <linux/memblock.h>
#include <linux/platform_device.h>
#include <linux/pstore_ram.h>
#include <linux/reboot.h>
#include <linux/pwm.h>
#include <linux/serial_core.h>
#include <linux/serial_s3c.h>
#include <linux/spi/spi.h>
#include <linux/spi/s3c24xx.h>
#include <linux/mtd/mtd.h>
#include <linux/mtd/rawnand.h>
#include <linux/mtd/partitions.h>
#include <linux/platform_data/mtd-nand-s3c2410.h>
#include <linux/platform_data/i2c-s3c2410.h>
#include <linux/platform_data/usb-s3c2410_udc.h>
#include <linux/platform_data/usb-ohci-s3c2410.h>
#include <sound/s3c24xx_uda134x.h>

#include <asm/mach/arch.h>
#include <asm/mach/map.h>
#include <asm/mach-types.h>

#include "regs-gpio.h"
#include "regs-clock.h"
#include "map-s3c24xx.h"
#include "gpio-samsung.h"
#include "gpio-cfg.h"
#include "devs.h"
#include "cpu.h"
#include "irqs.h"
#include "s3c24xx.h"
#include "hardware-s3c24xx.h"

static struct map_desc RabbitV3_iodesc[] __initdata = {
};

/* All three UARTs: PCLK, interrupt mode with RX timeout and RX error
 * interrupts, TX interrupt on level; 8N1; FIFOs on, RX trigger at 8 bytes,
 * TX at 16. (S3C2443_UCON_RXERR_IRQEN is bit 6, which the S3C2440 has too.) */
#define UCON	(S3C2410_UCON_TXILEVEL | S3C2410_UCON_RXFIFO_TOI | \
		 S3C2443_UCON_RXERR_IRQEN | S3C2410_UCON_TXIRQMODE | \
		 S3C2410_UCON_RXIRQMODE)
#define ULCON	(S3C2410_LCON_CS8 | S3C2410_LCON_PNONE)
#define UFCON	(S3C2440_UFCON_TXTRIG16 | S3C2440_UFCON_RXTRIG8 | \
		 S3C2410_UFCON_FIFOMODE)

/* The UPLL for USB: 12 MHz crystal x (MDIV + 8) / ((PDIV + 2) x 2^SDIV)
 * = 12 x 64 / (4 x 4) = 48 MHz, the value the BIOS writes at power-on and
 * 2.6.32 again at its map_io (mach-rabbitv3.c:84-91 of LapinLibre's tree;
 * the BIOS: 0x33f845b8 of Gengar's mtd0, (56, 2, 2) into UPLLCON). 6.1's
 * clock driver registers the UPLL without a rate table, so clk_set_rate()
 * on it changes nothing (clk-pll.c, samsung_s3c2410_upll_clk_min_ops has no
 * set_rate): the register is written here, as the two others did, and read
 * back (2026-11-24, FLASH-PLAN step 1). */
#define RABBITV3_UPLL_MDIV	56
#define RABBITV3_UPLL_PDIV	2
#define RABBITV3_UPLL_SDIV	2
#define RABBITV3_UPLLCON_48MHZ	((RABBITV3_UPLL_MDIV << 12) | (RABBITV3_UPLL_PDIV << 4) | \
				 RABBITV3_UPLL_SDIV)

/* ramoops: 1 MiB at 32 MiB, the kernel's console kept across a restart,
 * read by the next boot's autorun (karotz/kexec/ramoops.py). The kexec key
 * carved it out of RAM on the command line (mem=32M@0x30000000
 * mem=31M@0x32100000); from the BIOS the line is the BIOS's own, so the
 * board reserves it (FLASH-PLAN step 1, 2026-11-24). */
#define RABBITV3_RAMOOPS_BASE	0x32000000
#define RABBITV3_RAMOOPS_SIZE	SZ_1M
#define RABBITV3_RAMOOPS_CONSOLE	0xC0000
#define RABBITV3_RAMOOPS_RECORD	0x10000

/* NAND layout: each partition follows the previous one, except for an
 * unused 1 MB after "kernel rescue" and another after "ramdisk rescue"; the
 * last one runs to the end of the 256 MB chip. */
#define RABBITV3_NAND_BOOTLOADER_SIZE	SZ_256K
#define RABBITV3_NAND_KERNEL_SIZE	(3 * SZ_1M)
#define RABBITV3_NAND_RAMDISK_SIZE	(5 * SZ_1M)
#define RABBITV3_NAND_YAFFS_RESCUE_SIZE	(3 * SZ_1M)
#define RABBITV3_NAND_YAFFS_SIZE	(234 * SZ_1M)
#define RABBITV3_NAND_RESCUE_GAP	SZ_1M

#define RABBITV3_NAND_KERNEL_OFFSET	RABBITV3_NAND_BOOTLOADER_SIZE
#define RABBITV3_NAND_RAMDISK_OFFSET	(RABBITV3_NAND_KERNEL_OFFSET + RABBITV3_NAND_KERNEL_SIZE)
#define RABBITV3_NAND_KRESCUE_OFFSET	(RABBITV3_NAND_RAMDISK_OFFSET + RABBITV3_NAND_RAMDISK_SIZE)
#define RABBITV3_NAND_RRESCUE_OFFSET	(RABBITV3_NAND_KRESCUE_OFFSET + RABBITV3_NAND_KERNEL_SIZE + \
					 RABBITV3_NAND_RESCUE_GAP)
#define RABBITV3_NAND_YRESCUE_OFFSET	(RABBITV3_NAND_RRESCUE_OFFSET + RABBITV3_NAND_RAMDISK_SIZE + \
					 RABBITV3_NAND_RESCUE_GAP)
#define RABBITV3_NAND_YAFFS_OFFSET	(RABBITV3_NAND_YRESCUE_OFFSET + RABBITV3_NAND_YAFFS_RESCUE_SIZE)
#define RABBITV3_NAND_MYSTERY_OFFSET	(RABBITV3_NAND_YAFFS_OFFSET + RABBITV3_NAND_YAFFS_SIZE)
#define RABBITV3_NAND_MYSTERY_SIZE	(SZ_256M - RABBITV3_NAND_MYSTERY_OFFSET)

/* NAND timings, in ns. */
#define RABBITV3_NAND_TACLS	20
#define RABBITV3_NAND_TWRPH0	50
#define RABBITV3_NAND_TWRPH1	20

/* SPI0 and its one device, the LED driver, on chip select 1 (GPG2). */
#define RABBITV3_SPI_BUS	0
#define RABBITV3_SPI_LED_CS	0	/* 1 on 2.6.32: see the CS lookup below */
#define RABBITV3_SPI_LED_HZ	10000000	/* 10 MHz */
#define RABBITV3_SPI_NUM_CS	2

/* I2C: the controller's own slave address, and 400 kHz. */
#define RABBITV3_I2C_SLAVE_ADDR	0x10
#define RABBITV3_I2C_FREQ	400000

/* NAND: 256 MB, no ECC (the bootloader writes without it; YAFFS1 does its own). */
static struct mtd_partition RabbitV3_nand_part[] = {
	[0] = {
		.name		= "bootloader",
		.size		= RABBITV3_NAND_BOOTLOADER_SIZE,
		.offset		= 0,
		.mask_flags	= MTD_POWERUP_LOCK,
	},
	[1] = {
		.name		= "Kernel",
		.size		= RABBITV3_NAND_KERNEL_SIZE,
		.offset		= RABBITV3_NAND_KERNEL_OFFSET,
	},
	[2] = {
		.name		= "ramdisk",
		.size		= RABBITV3_NAND_RAMDISK_SIZE,
		.offset		= RABBITV3_NAND_RAMDISK_OFFSET,
	},
	[3] = {
		.name		= "kernel rescue",
		.size		= RABBITV3_NAND_KERNEL_SIZE,
		.offset		= RABBITV3_NAND_KRESCUE_OFFSET,
		.mask_flags	= MTD_POWERUP_LOCK,
	},
	[4] = {
		.name		= "ramdisk rescue",
		.size		= RABBITV3_NAND_RAMDISK_SIZE,
		.offset		= RABBITV3_NAND_RRESCUE_OFFSET,
		.mask_flags	= MTD_POWERUP_LOCK,
	},
	[5] = {
		.name		= "yaffs rescue",
		.size		= RABBITV3_NAND_YAFFS_RESCUE_SIZE,
		.offset		= RABBITV3_NAND_YRESCUE_OFFSET,
	},
	[6] = {
		.name		= "yaffs",
		.size		= RABBITV3_NAND_YAFFS_SIZE,
		.offset		= RABBITV3_NAND_YAFFS_OFFSET,
	},
	[7] = {
		.name		= "mystery box",
		.size		= RABBITV3_NAND_MYSTERY_SIZE,
		.offset		= RABBITV3_NAND_MYSTERY_OFFSET,
	},
};

static struct s3c2410_nand_set RabbitV3_nand_sets[] = {
	[0] = {
		.nr_chips	= 1,
		.nr_partitions	= ARRAY_SIZE(RabbitV3_nand_part),
		.name		= "NAND",
		.partitions	= RabbitV3_nand_part,
	},
};

static struct s3c2410_platform_nand RabbitV3_nand_info __initdata = {
	.tacls		= RABBITV3_NAND_TACLS,
	.twrph0		= RABBITV3_NAND_TWRPH0,
	.twrph1		= RABBITV3_NAND_TWRPH1,
	.nr_sets	= ARRAY_SIZE(RabbitV3_nand_sets),
	.sets		= RabbitV3_nand_sets,
	.engine_type	= NAND_ECC_ENGINE_TYPE_NONE,
};

/* SPI0: the LED driver, through spidev, on chip select 1 (GPG2). */
static struct spi_board_info RabbitV3_spi_board_info[] __initdata = {
	{
		.modalias	= "karotz-leds",	/* spidev, ../spidev-karotz.patch */
		.max_speed_hz	= RABBITV3_SPI_LED_HZ,
		.bus_num	= RABBITV3_SPI_BUS,
		.chip_select	= RABBITV3_SPI_LED_CS,
		.mode		= SPI_MODE_0,
	},
};

static struct s3c2410_spi_info RabbitV3_spi0_info = {
	.num_cs		= RABBITV3_SPI_NUM_CS,
	.bus_num	= RABBITV3_SPI_BUS,
};

/* The LED driver's chip select, GPG2. 6.1's SPI core asks for "cs" GPIOs
 * in the controller's name (spi0, not the platform device's) and from
 * index 0 up to as many as the table has: under "s3c2410-spi.0" at index 1,
 * it found none, GPG2 stayed nSS0 and the LED driver was never selected
 * (GPIO registers read on Gengar, 2026-11-24: GPGCON GPG2 11 under 6.1,
 * 01 under 2.6.32). Chip select 0 now; Terrier OS's mdev names the device
 * spidev0.1 as its led-daemon expects (karotz/kexec make-key.sh). */
static struct gpiod_lookup_table RabbitV3_spi0_gpio_table = {
	.dev_id = "spi0",
	.table = {
		GPIO_LOOKUP_IDX("GPIOG", 2, "cs", RABBITV3_SPI_LED_CS, GPIO_ACTIVE_LOW),
		{ },
	},
};

static struct s3c2410_platform_i2c RabbitV3_i2c_info __initdata = {
	.slave_addr	= RABBITV3_I2C_SLAVE_ADDR,
	.frequency	= RABBITV3_I2C_FREQ,
};

/* hwport is the UART's number. */
static struct s3c2410_uartcfg RabbitV3_uartcfgs[] __initdata = {
	[0] = {
		.hwport	     = 0,
		.flags	     = 0,
		.ucon	     = UCON,
		.ulcon	     = ULCON,
		.ufcon	     = UFCON,
	},
	[1] = {
		.hwport	     = 1,
		.flags	     = 0,
		.ucon	     = UCON,
		.ulcon	     = ULCON,
		.ufcon	     = UFCON,
	},
	[2] = {
		.hwport	     = 2,
		.flags	     = 0,
		.ucon	     = UCON,
		.ulcon	     = ULCON,
		.ufcon	     = UFCON,
	},
};

static struct s3c2410_hcd_info RabbitV3_usb_info __initdata = {
	.port[0]	= {
		.flags	= S3C_HCDFLG_USED,
	},
	.port[1]	= {
		.flags	= S3C_HCDFLG_USED,
	},
};

/* The head button, also as an input device (it is GPF0, like
 * /dev/rabbit_button). button-daemon reads /dev/rabbit_button (step 5). */
static struct gpio_keys_button RabbitV3_buttons[] = {
	{
		.code		= KEY_ENTER,
		.gpio		= S3C2410_GPF(0),
		.active_low	= 0,
		.desc		= "key_enter",
	},
};

static struct gpio_keys_platform_data RabbitV3_button_data = {
	.buttons	= RabbitV3_buttons,
	.nbuttons	= ARRAY_SIZE(RabbitV3_buttons),
};

static struct platform_device RabbitV3_button_device = {
	.name		= "gpio-keys",
	.id		= -1,
	.dev		= {
		.platform_data	= &RabbitV3_button_data,
	},
};

/* The UDA1341 codec, its L3 bus on GPB2-4. */
static struct s3c24xx_uda134x_platform_data RabbitV3_uda134x_data = {
	.l3_clk		= S3C2410_GPB(4),
	.l3_mode	= S3C2410_GPB(2),
	.l3_data	= S3C2410_GPB(3),
	.model		= UDA134X_UDA1341,
};

static struct platform_device RabbitV3_uda134x = {
	.name		= "s3c24xx_uda134x",
	.id		= 0,
	.dev		= {
		.platform_data	= &RabbitV3_uda134x_data,
	},
};

static struct uda134x_platform_data RabbitV3_uda134x_codec_data = {
	.l3 = {
		.gpio_clk	= S3C2410_GPB(4),
		.gpio_data	= S3C2410_GPB(3),
		.gpio_mode	= S3C2410_GPB(2),
		.use_gpios	= 1,
		.data_hold	= 1,
		.data_setup	= 1,
		.clock_high	= 1,
		.mode_hold	= 1,
		.mode		= 1,
		.mode_setup	= 1,
	},
	.model = UDA134X_UDA1341,
};

static struct platform_device RabbitV3_uda134x_codec = {
	.name		= "uda134x-codec",
	.id		= -1,
	.dev		= {
		.platform_data	= &RabbitV3_uda134x_codec_data,
	},
};

/* USB device: the D+ pull-up on GPG11. */
static struct s3c2410_udc_mach_info RabbitV3_udc_cfg __initdata = {
};

static struct gpiod_lookup_table RabbitV3_udc_gpio_table = {
	.dev_id = "s3c2410-usbgadget",
	.table = {
		GPIO_LOOKUP("GPIOG", 11, "pullup", GPIO_ACTIVE_HIGH),
		{ },
	},
};

#if IS_ENABLED(CONFIG_PSTORE_RAM)
static struct ramoops_platform_data RabbitV3_ramoops_data = {
	.mem_address	= RABBITV3_RAMOOPS_BASE,
	.mem_size	= RABBITV3_RAMOOPS_SIZE,
	.console_size	= RABBITV3_RAMOOPS_CONSOLE,
	.record_size	= RABBITV3_RAMOOPS_RECORD,
	.max_reason	= KMSG_DUMP_OOPS,
};

static struct platform_device RabbitV3_ramoops = {
	.name		= "ramoops",
	.id		= -1,
	.dev		= {
		.platform_data	= &RabbitV3_ramoops_data,
	},
};
#endif

static void __init RabbitV3_reserve(void)
{
	/* Reserved whether ramoops is built or not: the region keeps the last
	 * kernel's console for the next boot's autorun either way. */
	memblock_reserve(RABBITV3_RAMOOPS_BASE, RABBITV3_RAMOOPS_SIZE);
}

/* The Karotz's own character devices (rabbitv3-chardevs.c, step 5): the
 * head button, the power-on line, the ears. Their PWM channels by name. */
static struct platform_device RabbitV3_rabbit_button = { .name = "rabbit-button", .id = -1 };
static struct platform_device RabbitV3_rabbit_poweron = { .name = "rabbit-poweron", .id = -1 };
static struct platform_device RabbitV3_rabbit_ears = { .name = "rabbit-ears", .id = -1 };

static struct pwm_lookup RabbitV3_pwm_lookup[] = {
	PWM_LOOKUP("samsung-pwm", 0, "rabbit-ears", "left", 16320, PWM_POLARITY_NORMAL),
	PWM_LOOKUP("samsung-pwm", 1, "rabbit-ears", "right", 16320, PWM_POLARITY_NORMAL),
};

static struct platform_device *RabbitV3_devices[] __initdata = {
	&s3c_device_ohci,
	&s3c_device_wdt,
	&s3c_device_rtc,
	&s3c_device_i2c0,
	&s3c_device_iis,
	&s3c2440_device_dma,
	&s3c_device_spi0,
	&s3c_device_nand,
	&s3c_device_usbgadget,
	&RabbitV3_button_device,
	&samsung_device_pwm,
	&RabbitV3_rabbit_button,
	&RabbitV3_rabbit_poweron,
	&RabbitV3_rabbit_ears,
	&RabbitV3_uda134x_codec,
	&RabbitV3_uda134x,
};

/*
 * The USB host put in reset before a restart, as 2.6.32 left it (its
 * ohci_shutdown wrote HcControl with HCFS = USBRESET: the root ports drive
 * a bus reset to the internal hub, TUSB2046, which the board resets by no
 * other line we know of). 6.1's ohci_shutdown does a software reset
 * instead, after which the controller is suspended and the hub keeps
 * whatever state it had; after a warm restart (6.1 -> 2.6.32, or a kexec)
 * it sometimes came up without its devices until the mains (2026-11-24).
 * A reboot notifier runs for a reboot and for kexec alike
 * (kernel_restart_prepare), before the drivers' shutdown. Needs Gengar:
 * QEMU's mini2440 has no OHCI.
 */
#define RABBITV3_OHCI_CONTROL		0x04
#define RABBITV3_OHCI_CTRL_RWC		BIT(9)
#define RABBITV3_OHCI_RESET_MS		20

static int RabbitV3_usb_reset_notify(struct notifier_block *nb, unsigned long action, void *data)
{
	void __iomem *ohci;
	u32 was, now;

	ohci = ioremap(S3C2410_PA_USBHOST, SZ_256);
	if (!ohci)
		return NOTIFY_DONE;
	was = readl(ohci + RABBITV3_OHCI_CONTROL);
	writel(was & RABBITV3_OHCI_CTRL_RWC, ohci + RABBITV3_OHCI_CONTROL);
	now = readl(ohci + RABBITV3_OHCI_CONTROL);
	mdelay(RABBITV3_OHCI_RESET_MS);
	pr_info("rabbitv3: usb host in reset for the restart, HcControl %08x -> %08x\n", was, now);
	iounmap(ohci);
	return NOTIFY_DONE;
}

static struct notifier_block RabbitV3_usb_reset_nb = {
	.notifier_call	= RabbitV3_usb_reset_notify,
	.priority	= 0,
};

/*
 * The ports as 2.6.32's RabbitV3_map_io wrote them, pin by pin: GPA the
 * memory bus (GPA0 an output), GPB0-4 outputs without pull-ups (the ear
 * motors, the UDA1341's L3 bus), GPC and GPD inputs with pull-ups, GPE the
 * I2S (GPE0-3 without pull-ups), its data-out GPE4 an output and SPI0,
 * GPG the SPI chip selects nSS0/nSS1, SPICLK1, GPG9 an output and GPG11
 * EINT19, GPH UART0 and UART1. GPF is left as the bootloader set it.
 */
/* Ports A and B, logged around each call: on Gengar 6.1 stopped after
 * "init 0 start" and before "gpio A B" (2026-11-23), so in one of these
 * three; GPA is the memory bus's address and control lines. */
#define RV3_REGS(what) pr_info("rabbitv3: gpio %s GPACON %08x GPBCON %08x GPBUP %08x\n", what, \
	__raw_readl(S3C2410_GPACON), __raw_readl(S3C2410_GPBCON), __raw_readl(S3C2410_GPBUP))

static void __init RabbitV3_gpio_setup(void)
{
	RV3_REGS("before");
	s3c_gpio_cfgpin(S3C2410_GPA(0), S3C_GPIO_OUTPUT);
	RV3_REGS("GPA0");
	/* SFN(2), not SFN(1): 6.1's bank A maps SFN(1) to output and SFN(2) to
	 * the pin's function (s3c24xx_gpio_setcfg_abank, "Map output to 0, and
	 * SFN2 to 1"). SFN(1) turned the memory bus's address and control lines
	 * into outputs and froze Gengar at its first run (2026-11-23); QEMU
	 * models no bus and never noticed. */
	s3c_gpio_cfgpin_range(S3C2410_GPA(1), 22, S3C_GPIO_SFN(2));
	RV3_REGS("GPA1-22");

	s3c_gpio_cfgall_range(S3C2410_GPB(0), 5, S3C_GPIO_OUTPUT, S3C_GPIO_PULL_NONE);
	RV3_REGS("GPB0-4");

	pr_info("rabbitv3: gpio A B\n");
	s3c_gpio_cfgall_range(S3C2410_GPC(0), 16, S3C_GPIO_INPUT, S3C_GPIO_PULL_UP);
	s3c_gpio_cfgall_range(S3C2410_GPD(0), 16, S3C_GPIO_INPUT, S3C_GPIO_PULL_UP);

	pr_info("rabbitv3: gpio C D\n");
	s3c_gpio_cfgall_range(S3C2410_GPE(0), 4, S3C_GPIO_SFN(2), S3C_GPIO_PULL_NONE);
	/* GPE4 is I2SSDO, the data to the UDA1341. 2.6.32's I2S driver switched
	 * it there around each transfer; 6.1's touches no pin, and as an output
	 * at 0 the codec heard nothing: /dev/audio took every write and Gengar
	 * stayed silent (2026-11-24). */
	s3c_gpio_cfgpin(S3C2410_GPE(4), S3C_GPIO_SFN(2));
	s3c_gpio_cfgpin_range(S3C2410_GPE(11), 3, S3C_GPIO_SFN(2));

	pr_info("rabbitv3: gpio E\n");
	/* GPG2 is the LED driver's chip select, a GPIO the SPI core drives
	 * (above); GPG3 an input, as 2.6.32 leaves it (its camera pin). */
	s3c_gpio_cfgall_range(S3C2410_GPG(2), 1, S3C_GPIO_OUTPUT, S3C_GPIO_PULL_NONE);
	s3c_gpio_cfgall_range(S3C2410_GPG(3), 1, S3C_GPIO_INPUT, S3C_GPIO_PULL_NONE);
	s3c_gpio_cfgpin(S3C2410_GPG(7), S3C_GPIO_SFN(3));
	s3c_gpio_setpull(S3C2410_GPG(7), S3C_GPIO_PULL_NONE);
	s3c_gpio_cfgpin(S3C2410_GPG(9), S3C_GPIO_OUTPUT);
	s3c_gpio_setpull(S3C2410_GPG(9), S3C_GPIO_PULL_NONE);
	s3c_gpio_setpull(S3C2410_GPG(10), S3C_GPIO_PULL_NONE);
	s3c_gpio_cfgpin(S3C2410_GPG(11), S3C_GPIO_SFN(2));
	s3c_gpio_setpull(S3C2410_GPG(11), S3C_GPIO_PULL_NONE);

	pr_info("rabbitv3: gpio G\n");
	s3c_gpio_cfgall_range(S3C2410_GPH(1), 5, S3C_GPIO_SFN(2), S3C_GPIO_PULL_NONE);
	s3c_gpio_setpull(S3C2410_GPH(9), S3C_GPIO_PULL_NONE);
	s3c_gpio_setpull(S3C2410_GPH(10), S3C_GPIO_PULL_NONE);
}

static void __init RabbitV3_map_io(void)
{
	u32 upll;

	s3c24xx_init_io(RabbitV3_iodesc, ARRAY_SIZE(RabbitV3_iodesc));
	/* Before the clock driver reads it (init_time): 48 MHz for the USB,
	 * written only if the BIOS (or whatever ran before a kexec) left
	 * something else. The value seen is logged from RabbitV3_init. */
	upll = __raw_readl(S3C2410_UPLLCON);
	if (upll != RABBITV3_UPLLCON_48MHZ)
		__raw_writel(RABBITV3_UPLLCON_48MHZ, S3C2410_UPLLCON);
	s3c24xx_init_uarts(RabbitV3_uartcfgs, ARRAY_SIZE(RabbitV3_uartcfgs));
	s3c24xx_set_timer_source(S3C24XX_PWM3, S3C24XX_PWM4);
}

static void __init RabbitV3_init_time(void)
{
	s3c2440_init_clocks(12000000);
	s3c24xx_timer_init();
}

/*
 * One line per step of the board's init (2026-11-23): on Gengar, 6.1's
 * console, kept by ramoops, stopped after "ramoops: using ..." and before
 * "S3C2440: Initialising architecture" -- in here, most likely, the board's
 * first run on real hardware. The last line logged names the step.
 */
#define RV3_STEP(n, what) pr_info("rabbitv3: init %d %s\n", n, what)

static void __init RabbitV3_init(void)
{
	struct clk *spi;

	RV3_STEP(0, "start");
	/* The clocks as found: UPLLCON must read 00038022 (56/2/2, 48 MHz) on
	 * every boot, from the BIOS as after a kexec; CLKSLOW bit 7 set would
	 * be the UPLL off. Read on Gengar beside karotz/kexec/usb-measure. */
	pr_info("rabbitv3: clocks MPLLCON %08x UPLLCON %08x CLKCON %08x CLKSLOW %08x CLKDIVN %08x MISCCR %08x\n",
		__raw_readl(S3C2410_MPLLCON), __raw_readl(S3C2410_UPLLCON),
		__raw_readl(S3C2410_CLKCON), __raw_readl(S3C2410_CLKSLOW),
		__raw_readl(S3C2410_CLKDIVN), __raw_readl(S3C2410_MISCCR));
	RabbitV3_gpio_setup();
	RV3_STEP(1, "gpio");

	/* SPI0's clock: 6.1's S3C2440 clocks have the "spi" gate but no lookup
	 * for the controller ("s3c2410-spi.0: No clock for device" in QEMU,
	 * 2026-11-22: no 6.1 S3C2440 board uses it). */
	spi = __clk_lookup("spi");
	if (spi) {
		clk_register_clkdev(spi, "spi", "s3c2410-spi.0");
		/* spi-s3c24xx.c only ever calls clk_enable(), which an unprepared
		 * clock refuses ("Enabling unprepared spi", the LEDs' bus dead):
		 * prepared once here, for good. */
		clk_prepare(spi);
	}

	RV3_STEP(2, "spi clock");
	/* The UPLL was set in map_io (clk_set_rate on it is a no-op in 6.1);
	 * here only the check that the clock driver read it as 48 MHz. */
	{
		struct clk *upll = clk_get(NULL, "upll");

		if (!IS_ERR(upll)) {
			pr_info("rabbitv3: upll %lu Hz\n", clk_get_rate(upll));
			clk_put(upll);
		}
	}

	RV3_STEP(3, "upll");
	s3c_nand_set_platdata(&RabbitV3_nand_info);
	s3c_i2c0_set_platdata(&RabbitV3_i2c_info);
	s3c_ohci_set_platdata(&RabbitV3_usb_info);
	s3c_device_spi0.dev.platform_data = &RabbitV3_spi0_info;
	gpiod_add_lookup_table(&RabbitV3_spi0_gpio_table);
	gpiod_add_lookup_table(&RabbitV3_udc_gpio_table);
	s3c24xx_udc_set_platdata(&RabbitV3_udc_cfg);

	RV3_STEP(4, "platdata");
	s3c2410_modify_misccr(S3C2410_MISCCR_USBHOST | S3C2410_MISCCR_USBSUSPND0 |
			      S3C2410_MISCCR_USBSUSPND1, 0);

	RV3_STEP(5, "misccr usb host");
	pwm_add_table(RabbitV3_pwm_lookup, ARRAY_SIZE(RabbitV3_pwm_lookup));
	platform_add_devices(RabbitV3_devices, ARRAY_SIZE(RabbitV3_devices));
#if IS_ENABLED(CONFIG_PSTORE_RAM)
	/* On its own: with ramoops.* also on the kexec line, the parameters'
	 * device exists first and this one is a duplicate; inside the list the
	 * failure made platform_add_devices unregister every device before it,
	 * the USB host among them (Gengar, 2026-11-25: no hub, no network). */
	if (platform_device_register(&RabbitV3_ramoops))
		pr_info("rabbitv3: ramoops already registered\n");
#endif
	RV3_STEP(6, "devices");
	spi_register_board_info(RabbitV3_spi_board_info, ARRAY_SIZE(RabbitV3_spi_board_info));
	register_reboot_notifier(&RabbitV3_usb_reset_nb);
	RV3_STEP(7, "done");
}

MACHINE_START(RABBITV3, "RABBITV3 (KAROTZ)")
	.atag_offset	= 0x100,
	.nr_irqs	= NR_IRQS_S3C2440,
	.map_io		= RabbitV3_map_io,
	.reserve	= RabbitV3_reserve,
	.init_machine	= RabbitV3_init,
	.init_irq	= s3c2440_init_irq,
	.init_time	= RabbitV3_init_time,
MACHINE_END

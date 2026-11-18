// SPDX-License-Identifier: GPL-2.0
/*
 * Stand-ins for the Karotz's Pragmatec drivers, so FROS's daemons start in
 * QEMU (karotz/fros, 2026-11-17).
 *
 * The rabbit's linuxrc makes the nodes itself (c 52 0 button, c 53 0 power,
 * c 60 0 ears); the LED daemon talks to /dev/spidev0.1, which mdev makes
 * from the class device below. Nothing here is the real hardware's
 * protocol, which no source describes: each device opens, a read waits
 * (a button nobody presses), and every write and ioctl is printed with its
 * bytes, so the console shows what FROS asks of its rabbit. The LED's SPI
 * transfers are printed with their transmit bytes: that is the colour.
 */
#include <linux/module.h>
#include <linux/fs.h>
#include <linux/device.h>
#include <linux/uaccess.h>
#include <linux/wait.h>
#include <linux/slab.h>
#include <linux/ioctl.h>

#define KZ_BUTTON 52
#define KZ_POWER  53
#define KZ_EARS   60
#define KZ_SPI    153		/* spidev's major; spidev itself is not built */

static DECLARE_WAIT_QUEUE_HEAD(kz_never);

static const char *kz_name(struct file *f)
{
	switch (imajor(file_inode(f))) {
	case KZ_BUTTON: return "button";
	case KZ_POWER:  return "power";
	case KZ_EARS:   return "ears";
	default:        return "led (spidev0.1)";
	}
}

static int kz_open(struct inode *i, struct file *f)
{
	pr_info("karotz: %s opened by %s\n", kz_name(f), current->comm);
	return 0;
}

static ssize_t kz_read(struct file *f, char __user *buf, size_t n, loff_t *off)
{
	if (f->f_flags & O_NONBLOCK)
		return -EAGAIN;
	/* Nobody presses, turns or unplugs anything. */
	if (wait_event_interruptible(kz_never, 0))
		return -ERESTARTSYS;
	return 0;
}

static void kz_dump(const char *who, const char *what, const void __user *p, size_t n)
{
	u8 b[32];
	size_t k = min(n, sizeof(b));

	if (!p || copy_from_user(b, p, k))
		pr_info("karotz: %s %s, %zu bytes\n", who, what, n);
	else
		pr_info("karotz: %s %s, %zu bytes: %*phN\n", who, what, n, (int)k, b);
}

static ssize_t kz_write(struct file *f, const char __user *buf, size_t n, loff_t *off)
{
	kz_dump(kz_name(f), "write", buf, n);
	return n;
}

/* struct spi_ioc_transfer, 32 bytes: tx_buf, rx_buf, len, ... */
struct kz_spi_xfer {
	__u64 tx_buf, rx_buf;
	__u32 len, speed_hz;
	__u16 delay_usecs;
	__u8 bits_per_word, cs_change, tx_nbits, rx_nbits, word_delay_usecs, pad;
};

static long kz_ioctl(struct file *f, unsigned int cmd, unsigned long arg)
{
	if (imajor(file_inode(f)) == KZ_SPI && _IOC_TYPE(cmd) == 'k' && _IOC_NR(cmd) == 0) {
		/* SPI_IOC_MESSAGE(n) */
		unsigned int n = _IOC_SIZE(cmd) / sizeof(struct kz_spi_xfer), i;
		struct kz_spi_xfer x;

		for (i = 0; i < n; i++) {
			if (copy_from_user(&x, (void __user *)arg + i * sizeof(x), sizeof(x)))
				return -EFAULT;
			kz_dump("led", "spi", u64_to_user_ptr(x.tx_buf), x.len);
		}
		return 0;
	}
	pr_info("karotz: %s ioctl %08x arg %08lx\n", kz_name(f), cmd, arg);
	return 0;
}

static const struct file_operations kz_fops = {
	.owner = THIS_MODULE,
	.open = kz_open,
	.read = kz_read,
	.write = kz_write,
	.unlocked_ioctl = kz_ioctl,
	.llseek = noop_llseek,
};

static int __init kz_init(void)
{
	static const struct { int major; const char *name; } devs[] = {
		{ KZ_BUTTON, "rabbit_button" }, { KZ_POWER, "rabbit_power" },
		{ KZ_EARS, "rabbit_ears" }, { KZ_SPI, "spidev" },
	};
	struct class *cls;
	int i, err;

	for (i = 0; i < ARRAY_SIZE(devs); i++) {
		err = register_chrdev(devs[i].major, devs[i].name, &kz_fops);
		if (err < 0)
			pr_err("karotz: major %d: %d\n", devs[i].major, err);
	}
	cls = class_create("spidev");
	if (!IS_ERR(cls))
		device_create(cls, NULL, MKDEV(KZ_SPI, 1), NULL, "spidev0.1");
	pr_info("karotz: stand-ins for button, power, ears and the LED\n");
	return 0;
}
device_initcall(kz_init);
MODULE_LICENSE("GPL");

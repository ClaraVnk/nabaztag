// SPDX-License-Identifier: GPL-2.0-only
/*
 * The Karotz's own character devices, ported from LapinLibre's 2.6.32
 * drivers/char/rabbit_{button,poweron,ears}.c to Linux 6.1 (Terrier OS,
 * step 5 of docs/TERRIER-OS-KERNEL.md).
 *
 * The interface is the 2.6.32 one, byte for byte, so the rootfs (linuxrc's
 * mknod, button-daemon, power, the ears tools) runs unchanged:
 *
 *   /dev/rabbit_button  (major 52) the head button on GPF0: every edge
 *       queues '1' (pressed, the pin low) or '0' (released) in 16 bytes,
 *       the oldest dropped when full; read() takes what is queued without
 *       blocking, poll() says when there is some.
 *   /dev/rabbit_poweron (major 53) one byte, '1' when GPC2 reads low.
 *   /dev/rabbit_ears    (major 60) the two ear motors: PWM0/TOUT0 and
 *       GPF4 for the left, PWM1/TOUT1 and GPF5 for the right, an encoder
 *       step on EINT1 (left) and EINT2 (right). ioctl type 'E':
 *         1 _IOW  move the right ear, struct { int pwm; int pos; }
 *         2 _IOW  the same for the left ear
 *         3 _IOR  the step counters { right, left };
 *           _IOW  zero each counter given as 0
 *         4 _IOR  the status bits; _IOW clear status bits (bits 0-1 kept)
 *       Status: 1 left moving, 2 right moving, 4/8 left/right moved without
 *       being driven or stepped faster than 7 x 20 ms. read() hands out one
 *       byte per step: the time since the previous one in 20 ms units (at
 *       most 127), bit 7 set for the left ear.
 *
 * What changed under the interface, and why:
 * - 2.6.32's ioctl dereferenced the user pointer; this copies.
 * - The encoder handlers run threaded: 6.1's pwm_config() may sleep, and
 *   the step that reaches the target stops the motor (2.6.32 did that from
 *   the hard interrupt).
 * - The PWM channels come from a lookup in the board file (pwm_get), not
 *   from pwm_request() by number; the queues and status take a spinlock.
 * - The button's interrupt is shared: gpio-keys holds GPF0 as well.
 * - The encoders' trigger is left as the bootloader set it, as 2.6.32 did
 *   (request_irq without a trigger type). Not yet measured on Gengar.
 */
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/fs.h>
#include <linux/gpio.h>
#include <linux/interrupt.h>
#include <linux/ktime.h>
#include <linux/math64.h>
#include <linux/platform_device.h>
#include <linux/poll.h>
#include <linux/pwm.h>
#include <linux/spinlock.h>
#include <linux/uaccess.h>
#include <linux/wait.h>

#include "gpio-samsung.h"
#include "gpio-cfg.h"
#include "irqs.h"

/* --- the head button ---------------------------------------------------- */

#define RABBIT_BUTTON_MAJOR	52
#define RABBIT_BUTTON_NAME	"rabbit_button"
#define BUTTON_GPIO		S3C2410_GPF(0)
#define BUTTON_BUFFER		16

/* The byte queued for each state; the pin reads low while pressed. */
#define BUTTON_PRESSED		'1'
#define BUTTON_RELEASED		'0'

static DEFINE_SPINLOCK(button_lock);
static DECLARE_WAIT_QUEUE_HEAD(button_wait);
static char button_buffer[BUTTON_BUFFER];
static int button_count;

static __poll_t rabbit_button_poll(struct file *file, poll_table *wait)
{
	poll_wait(file, &button_wait, wait);
	return READ_ONCE(button_count) ? EPOLLIN | EPOLLRDNORM : 0;
}

static ssize_t rabbit_button_read(struct file *file, char __user *buf,
				  size_t len, loff_t *ppos)
{
	char out[BUTTON_BUFFER];
	unsigned long flags;
	int n;

	spin_lock_irqsave(&button_lock, flags);
	n = min_t(size_t, button_count, len);
	memcpy(out, button_buffer, n);
	memmove(button_buffer, button_buffer + n, button_count - n);
	button_count -= n;
	spin_unlock_irqrestore(&button_lock, flags);

	if (n && copy_to_user(buf, out, n))
		return -EFAULT;
	return n;
}

static const struct file_operations rabbit_button_fops = {
	.owner	= THIS_MODULE,
	.read	= rabbit_button_read,
	.poll	= rabbit_button_poll,
	.llseek	= noop_llseek,
};

static irqreturn_t rabbit_button_irq(int irq, void *dev_id)
{
	char state = gpio_get_value(BUTTON_GPIO) ? BUTTON_RELEASED : BUTTON_PRESSED;
	unsigned long flags;

	spin_lock_irqsave(&button_lock, flags);
	if (button_count == BUTTON_BUFFER) {
		memmove(button_buffer, button_buffer + 1, BUTTON_BUFFER - 1);
		button_count--;
	}
	button_buffer[button_count++] = state;
	spin_unlock_irqrestore(&button_lock, flags);
	wake_up_interruptible(&button_wait);
	return IRQ_HANDLED;
}

static int rabbit_button_probe(struct platform_device *pdev)
{
	int irq = gpio_to_irq(BUTTON_GPIO);
	int ret;

	if (irq < 0)
		return irq;
	ret = devm_request_irq(&pdev->dev, irq, rabbit_button_irq,
			       IRQF_SHARED | IRQF_TRIGGER_RISING | IRQF_TRIGGER_FALLING,
			       RABBIT_BUTTON_NAME, pdev);
	if (ret)
		return ret;
	ret = register_chrdev(RABBIT_BUTTON_MAJOR, RABBIT_BUTTON_NAME, &rabbit_button_fops);
	if (ret)
		return ret;
	dev_info(&pdev->dev, "/dev/rabbit_button (%d), GPF0 irq %d\n",
		 RABBIT_BUTTON_MAJOR, irq);
	return 0;
}

static int rabbit_button_remove(struct platform_device *pdev)
{
	unregister_chrdev(RABBIT_BUTTON_MAJOR, RABBIT_BUTTON_NAME);
	return 0;
}

static struct platform_driver rabbit_button_driver = {
	.probe	= rabbit_button_probe,
	.remove	= rabbit_button_remove,
	.driver	= { .name = "rabbit-button" },
};

/* --- the power-on line --------------------------------------------------- */

#define RABBIT_POWERON_MAJOR	53
#define RABBIT_POWERON_NAME	"rabbit_poweron"
#define POWERON_GPIO		S3C2410_GPC(2)

static ssize_t rabbit_poweron_read(struct file *file, char __user *buf,
				   size_t len, loff_t *ppos)
{
	char state = gpio_get_value(POWERON_GPIO) ? '0' : '1';

	if (!len)
		return 0;
	if (copy_to_user(buf, &state, 1))
		return -EFAULT;
	return 1;
}

static const struct file_operations rabbit_poweron_fops = {
	.owner	= THIS_MODULE,
	.read	= rabbit_poweron_read,
	.llseek	= noop_llseek,
};

static int rabbit_poweron_probe(struct platform_device *pdev)
{
	int ret = register_chrdev(RABBIT_POWERON_MAJOR, RABBIT_POWERON_NAME,
				  &rabbit_poweron_fops);

	if (!ret)
		dev_info(&pdev->dev, "/dev/rabbit_poweron (%d), GPC2\n",
			 RABBIT_POWERON_MAJOR);
	return ret;
}

static int rabbit_poweron_remove(struct platform_device *pdev)
{
	unregister_chrdev(RABBIT_POWERON_MAJOR, RABBIT_POWERON_NAME);
	return 0;
}

static struct platform_driver rabbit_poweron_driver = {
	.probe	= rabbit_poweron_probe,
	.remove	= rabbit_poweron_remove,
	.driver	= { .name = "rabbit-poweron" },
};

/* --- the ears ------------------------------------------------------------ */

#define RABBIT_EARS_MAJOR	60
#define RABBIT_EARS_NAME	"rabbit_ears"
#define RABBIT_EARS_MAGIC	'E'
#define RABBIT_EARS_MOVE_RIGHT	1
#define RABBIT_EARS_MOVE_LEFT	2
#define RABBIT_EARS_POSITION	3
#define RABBIT_EARS_STATUS	4

#define EAR_LEFT_MOVING		1
#define EAR_RIGHT_MOVING	2
#define EAR_LEFT_BLOCKED	4
#define EAR_RIGHT_BLOCKED	8

/* The duty cycle in ns is the speed (|pwm|) times 64 over a 16.32 us
 * period; a stopped motor is left at speed 1, as 2.6.32 left it. */
#define PWM_PERIOD_NS		16320
#define PWM_DUTY_SHIFT		6
#define PWM_SPEED_STOPPED	1
/* The fastest speed whose duty fits the period (255 x 64 = 16320 ns): a
 * larger |pwm| from user space made pwm_config() refuse the duty (-EINVAL)
 * and the ear kept its previous speed, direction line flipped (review of
 * 2026-11-24). ears-daemon sends 1 to 255 (LapinLibre WEB_EAR_SPEED 100). */
#define PWM_SPEED_MAX		(PWM_PERIOD_NS >> PWM_DUTY_SHIFT)

#define EAR_DIR_FORWARD		0
#define EAR_DIR_BACKWARD	1

#define EAR_STEP_UNIT_US	20000
#define EAR_STEP_FAST		7
#define EAR_STEP_LEFT		0x80
#define EAR_STEP_TIME_MAX	127
#define EARS_BUFFER		32

/* The order of the counters in RABBIT_EARS_POSITION's data. */
#define EAR_RIGHT		0
#define EAR_LEFT		1

struct ear_move {
	int pwm;
	int pos;
};

struct ear {
	const char	*name;		/* the PWM lookup's con_id */
	unsigned int	dir_gpio;
	unsigned int	step_gpio;
	int		moving, blocked;	/* status bits */
	u8		step_mark;	/* bit 7 of its step bytes */
	struct pwm_device *pwm;
	int		direction, target, position;
	ktime_t		last;
};

static struct ear ears[2] = {
	[EAR_RIGHT] = {
		.name = "right", .dir_gpio = S3C2410_GPF(5), .step_gpio = S3C2410_GPF(2),
		.moving = EAR_RIGHT_MOVING, .blocked = EAR_RIGHT_BLOCKED, .direction = 1,
	},
	[EAR_LEFT] = {
		.name = "left", .dir_gpio = S3C2410_GPF(4), .step_gpio = S3C2410_GPF(1),
		.moving = EAR_LEFT_MOVING, .blocked = EAR_LEFT_BLOCKED,
		.step_mark = EAR_STEP_LEFT, .direction = 1,
	},
};

static DEFINE_SPINLOCK(ears_lock);
static DEFINE_MUTEX(ears_move_lock);	/* pwm_config() sleeps */
static int ears_status = EAR_LEFT_BLOCKED | EAR_RIGHT_BLOCKED;
static char ears_buffer[EARS_BUFFER];
static int ears_count;

/* Run an ear at a signed speed, 0 to stop it. Called with ears_move_lock. */
static void ear_run(struct ear *e, int speed)
{
	unsigned long flags;

	spin_lock_irqsave(&ears_lock, flags);
	ears_status |= e->moving;
	if (speed > 0) {
		e->last = ktime_get();
		gpio_set_value(e->dir_gpio, EAR_DIR_FORWARD);
		e->direction = 1;
	} else if (speed == 0) {
		ears_status &= ~e->moving;
		speed = PWM_SPEED_STOPPED;
	} else {
		e->last = ktime_get();
		gpio_set_value(e->dir_gpio, EAR_DIR_BACKWARD);
		e->direction = -1;
		speed = -speed;
	}
	spin_unlock_irqrestore(&ears_lock, flags);
	pwm_config(e->pwm, speed << PWM_DUTY_SHIFT, PWM_PERIOD_NS);
	pwm_enable(e->pwm);
}

static irqreturn_t ear_step(int irq, void *dev_id)
{
	struct ear *e = dev_id;
	unsigned long flags;
	bool arrived;
	ktime_t now;
	int units;

	mutex_lock(&ears_move_lock);
	spin_lock_irqsave(&ears_lock, flags);
	if (!(ears_status & e->moving))
		ears_status |= e->blocked;
	if (e->direction > 0)
		arrived = ++e->position >= e->target;
	else
		arrived = --e->position <= e->target;
	now = ktime_get();
	/* div_s64: no 64-bit division helper on ARMv4T without libgcc. */
	units = min_t(s64, div_s64(ktime_us_delta(now, e->last), EAR_STEP_UNIT_US), INT_MAX);
	e->last = now;
	if (units < EAR_STEP_FAST)
		ears_status |= e->blocked;
	if (ears_count == EARS_BUFFER) {
		memmove(ears_buffer, ears_buffer + 1, EARS_BUFFER - 1);
		ears_count--;
	}
	ears_buffer[ears_count++] = min(units, EAR_STEP_TIME_MAX) | e->step_mark;
	spin_unlock_irqrestore(&ears_lock, flags);

	if (arrived) {
		/* 2.6.32 flipped the direction line before stopping. */
		gpio_set_value(e->dir_gpio, e->direction > 0 ? EAR_DIR_BACKWARD : EAR_DIR_FORWARD);
		ear_run(e, 0);
	}
	mutex_unlock(&ears_move_lock);
	return IRQ_HANDLED;
}

static ssize_t rabbit_ears_read(struct file *file, char __user *buf,
				size_t len, loff_t *ppos)
{
	char out[EARS_BUFFER];
	unsigned long flags;
	int n;

	spin_lock_irqsave(&ears_lock, flags);
	n = min_t(size_t, ears_count, len);
	memcpy(out, ears_buffer, n);
	memmove(ears_buffer, ears_buffer + n, ears_count - n);
	ears_count -= n;
	spin_unlock_irqrestore(&ears_lock, flags);

	if (n && copy_to_user(buf, out, n))
		return -EFAULT;
	return n;
}

static long rabbit_ears_ioctl(struct file *file, unsigned int cmd, unsigned long arg)
{
	void __user *uarg = (void __user *)arg;
	unsigned long flags;
	struct ear_move move;
	struct ear *e;
	int values[2], speed;

	if (_IOC_TYPE(cmd) != RABBIT_EARS_MAGIC)
		return -ENOTTY;

	switch (_IOC_NR(cmd)) {
	case RABBIT_EARS_MOVE_RIGHT:
	case RABBIT_EARS_MOVE_LEFT:
		if (!(_IOC_DIR(cmd) & _IOC_WRITE))
			return -EFAULT;
		if (copy_from_user(&move, uarg, sizeof(move)))
			return -EFAULT;
		e = &ears[_IOC_NR(cmd) == RABBIT_EARS_MOVE_RIGHT ? EAR_RIGHT : EAR_LEFT];
		/* |pwm| within the period; INT_MIN has no absolute value. */
		speed = move.pwm == INT_MIN ? PWM_SPEED_MAX : min(abs(move.pwm), PWM_SPEED_MAX);
		mutex_lock(&ears_move_lock);
		e->target = move.pos;
		if (e->position < e->target)
			ear_run(e, speed);
		else if (e->position > e->target)
			ear_run(e, -speed);
		else
			ear_run(e, 0);
		mutex_unlock(&ears_move_lock);
		return 0;

	case RABBIT_EARS_POSITION:
		if (_IOC_DIR(cmd) & _IOC_READ) {
			values[EAR_RIGHT] = READ_ONCE(ears[EAR_RIGHT].position);
			values[EAR_LEFT] = READ_ONCE(ears[EAR_LEFT].position);
			return copy_to_user(uarg, values, sizeof(values)) ? -EFAULT : 0;
		}
		if (_IOC_DIR(cmd) & _IOC_WRITE) {
			if (copy_from_user(values, uarg, sizeof(values)))
				return -EFAULT;
			spin_lock_irqsave(&ears_lock, flags);
			if (values[EAR_RIGHT] == 0)
				ears[EAR_RIGHT].position = 0;
			if (values[EAR_LEFT] == 0)
				ears[EAR_LEFT].position = 0;
			spin_unlock_irqrestore(&ears_lock, flags);
			return 0;
		}
		return -EFAULT;

	case RABBIT_EARS_STATUS:
		if (_IOC_DIR(cmd) & _IOC_READ) {
			values[0] = READ_ONCE(ears_status);
			return copy_to_user(uarg, values, sizeof(int)) ? -EFAULT : 0;
		}
		if (_IOC_DIR(cmd) & _IOC_WRITE) {
			if (copy_from_user(values, uarg, sizeof(int)))
				return -EFAULT;
			spin_lock_irqsave(&ears_lock, flags);
			ears_status &= values[0] | EAR_LEFT_MOVING | EAR_RIGHT_MOVING;
			spin_unlock_irqrestore(&ears_lock, flags);
			return 0;
		}
		return -EFAULT;
	}
	/* As 2.6.32: past 4, -ENOTTY; an unknown number below it, 0. */
	return _IOC_NR(cmd) > RABBIT_EARS_STATUS ? -ENOTTY : 0;
}

static const struct file_operations rabbit_ears_fops = {
	.owner		= THIS_MODULE,
	.read		= rabbit_ears_read,
	.unlocked_ioctl	= rabbit_ears_ioctl,
	.llseek		= noop_llseek,
};

static int rabbit_ears_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	int i, irq, ret;

	/* TOUT0 and TOUT1 drive the motors (the board file left GPB0/1 outputs). */
	s3c_gpio_cfgpin(S3C2410_GPB(0), S3C_GPIO_SFN(2));
	s3c_gpio_cfgpin(S3C2410_GPB(1), S3C_GPIO_SFN(2));

	for (i = 0; i < ARRAY_SIZE(ears); i++) {
		struct ear *e = &ears[i];

		ret = devm_gpio_request_one(dev, e->dir_gpio, GPIOF_OUT_INIT_LOW, e->name);
		if (ret)
			return dev_err_probe(dev, ret, "%s ear direction line\n", e->name);
		e->pwm = devm_pwm_get(dev, e->name);
		if (IS_ERR(e->pwm))
			return dev_err_probe(dev, PTR_ERR(e->pwm), "%s ear PWM\n", e->name);
		pwm_config(e->pwm, PWM_SPEED_STOPPED << PWM_DUTY_SHIFT, PWM_PERIOD_NS);
		pwm_enable(e->pwm);

		irq = gpio_to_irq(e->step_gpio);
		if (irq < 0)
			return irq;
		ret = devm_request_threaded_irq(dev, irq, NULL, ear_step, IRQF_ONESHOT,
						i == EAR_LEFT ? "Rabbit Left ear IT" :
								"Rabbit Right Ear IT", e);
		if (ret)
			return dev_err_probe(dev, ret, "%s ear encoder irq %d\n", e->name, irq);
	}
	ret = register_chrdev(RABBIT_EARS_MAJOR, RABBIT_EARS_NAME, &rabbit_ears_fops);
	if (ret)
		return ret;
	dev_info(dev, "/dev/rabbit_ears (%d), PWM0/1, EINT1/2\n", RABBIT_EARS_MAJOR);
	return 0;
}

static int rabbit_ears_remove(struct platform_device *pdev)
{
	unregister_chrdev(RABBIT_EARS_MAJOR, RABBIT_EARS_NAME);
	pwm_disable(ears[EAR_LEFT].pwm);
	pwm_disable(ears[EAR_RIGHT].pwm);
	return 0;
}

static struct platform_driver rabbit_ears_driver = {
	.probe	= rabbit_ears_probe,
	.remove	= rabbit_ears_remove,
	.driver	= { .name = "rabbit-ears" },
};

static struct platform_driver * const rabbitv3_drivers[] = {
	&rabbit_button_driver,
	&rabbit_poweron_driver,
	&rabbit_ears_driver,
};

static int __init rabbitv3_chardevs_init(void)
{
	return platform_register_drivers(rabbitv3_drivers, ARRAY_SIZE(rabbitv3_drivers));
}
module_init(rabbitv3_chardevs_init);

static void __exit rabbitv3_chardevs_exit(void)
{
	platform_unregister_drivers(rabbitv3_drivers, ARRAY_SIZE(rabbitv3_drivers));
}
module_exit(rabbitv3_chardevs_exit);

MODULE_DESCRIPTION("Karotz button, power-on and ears (/dev/rabbit_*)");
MODULE_LICENSE("GPL");

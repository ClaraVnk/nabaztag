/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * rabbitv3-camif.c - the Karotz's camera on Linux 6.1 (Terrier OS): LapinLibre's
 * 2.6.32 s3c2440camif.c, ported as the ears and the button were
 * (rabbitv3-chardevs.c): the same misc device /dev/camera, the same read()
 * and resolution ioctls, so webcam-daemon and the API's snapshot run
 * unchanged. Under 6.1 the camera did not exist at all (Gengar, 2026-11-26:
 * "the camera gave no picture"). What changed under the interface: gpiolib
 * and s3c_gpio_cfgpin for the pins, clk_prepare_enable, CAMDIVN written for
 * the sensor's 6 MHz, coherent image buffers, read() bounded by a buffer,
 * unlocked_ioctl.
 *
 * The original header follows.
 *
 * s3c2440camif.c - S3C2440 camera interface driver with the OV7675 sensor
 *
 * RABBITV3 (Karotz): the FriendlyARM mini2440 camif driver (sunxq), with its
 * soft SCCB bus and sensor parts in one file, reworked for the
 * OV7675: two image buffers allocated once at boot, a misc device with a
 * resolution ioctl (VGA or QVGA), and the sensor on GPG3/GPG5/GPG7.
 */
#include <linux/module.h>
#include <linux/clk.h>
#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/errno.h>
#include <linux/fs.h>
#include <linux/gpio.h>
#include <linux/init.h>
#include <linux/interrupt.h>
#include <linux/io.h>
#include <linux/ioport.h>
#include <linux/kernel.h>
#include <linux/miscdevice.h>
#include <linux/mm.h>
#include <linux/platform_device.h>
#include <linux/sched.h>
#include <linux/semaphore.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/wait.h>

#include "gpio-samsung.h"
#include "gpio-cfg.h"
#include "irqs.h"
#include "map-s3c24xx.h"
#include "regs-clock-s3c24xx.h"

struct video_device;

#include "rabbitv3-camif.h"

/* The OV7675's SCCB bus on GPG7 (clock) and GPG3 (data), bit-banged
 * (LapinLibre's sccb.h), through gpiolib. */
#define SIO_C	S3C2410_GPG(7)
#define SIO_D	S3C2410_GPG(3)

#define State(x)	gpio_get_value(x)
#define High(x)		do { gpio_set_value(x, 1); smp_mb(); } while (0)
#define Low(x)		do { gpio_set_value(x, 0); smp_mb(); } while (0)

#define WAIT_STABLE()	udelay(10)
#define WAIT_CYCLE()	udelay(90)

#define CFG_READ(x)	do { s3c_gpio_cfgpin(x, S3C_GPIO_INPUT); smp_mb(); } while (0)
#define CFG_WRITE(x)	do { s3c_gpio_cfgpin(x, S3C_GPIO_OUTPUT); smp_mb(); } while (0)

static void sccb_write(u8 IdAddr, u8 SubAddr, u8 data);
static u8 sccb_read(u8 IdAddr, u8 SubAddr);

/* debug print macro. */

/* hardware & driver name, version etc. */
#define CARD_NAME		"camera"

static unsigned has_ov7675;
unsigned long camif_base_addr;
/* The device the image buffers are allocated for (dma_alloc_coherent). */
static struct platform_device *camif_pdev;
static size_t img_buff_size;

/* camera device(s) */
static struct s3c2440camif_dev camera;


/* image buffer for previewing. */
struct s3c2440camif_buffer img_buff[CAMIF_NR_BUFFERS] =
{
	{
		.state = CAMIF_BUFF_INVALID,
		.img_size = 0,
		.order = 0,
		.virt_base = (unsigned long)NULL,
		.phy_base = (unsigned long)NULL
	},
	{
		.state = CAMIF_BUFF_INVALID,
		.img_size = 0,
		.order = 0,
		.virt_base = (unsigned long)NULL,
		.phy_base = (unsigned long)NULL
	}
};

/* software reset camera interface. */
static void __inline__ soft_reset_camif(void)
{
	u32 cigctrl;

	cigctrl = (1<<31)|(1<<29);
	iowrite32(cigctrl, S3C244X_CIGCTRL);
	mdelay(10);

	cigctrl = (1<<29);
	iowrite32(cigctrl, S3C244X_CIGCTRL);
	mdelay(10);
}

/* RABBITV3: CIGCTRL's camera pixel clock polarity bit, which the hardware reset sets. */
#define CIGCTRL_INVPOLCAMPCLK	(1<<26)

/* software reset camera interface. */
static void __inline__ hw_reset_camif(void)
{
	u32 cigctrl;

	cigctrl = (1<<30)|(1<<29)|CIGCTRL_INVPOLCAMPCLK;
	iowrite32(cigctrl, S3C244X_CIGCTRL);
	mdelay(10);

	cigctrl = (1<<29)|CIGCTRL_INVPOLCAMPCLK;
	iowrite32(cigctrl, S3C244X_CIGCTRL);
	mdelay(10);

}

/* switch camif from codec path to preview path. */
static void __inline__ camif_c2p(struct s3c2440camif_dev * pdev)
{
	/* 1. stop codec. */
	{
		u32 cicoscctrl;
		cicoscctrl = ioread32(S3C244X_CICOSCCTRL);
		cicoscctrl &= ~(1<<15);	// stop preview scaler.
		iowrite32(cicoscctrl, S3C244X_CICOSCCTRL);
	}

	/* 2. soft-reset camif. */
	soft_reset_camif();

	/* 3. clear all overflow. */
	{
		u32 ciwdofst;
		ciwdofst = ioread32(S3C244X_CIWDOFST);
		ciwdofst |= (1<<30)|(1<<15)|(1<<14)|(1<<13)|(1<<12);
		iowrite32(ciwdofst, S3C244X_CIWDOFST);

		ciwdofst &= ~((1<<30)|(1<<15)|(1<<14)|(1<<13)|(1<<12));
		iowrite32(ciwdofst, S3C244X_CIWDOFST);
	}
}

/* calculate main burst size and remained burst size. */
static void __inline__ calc_burst_size(u32 pixperword,u32 hSize, u32 *mainBurstSize, u32 *remainedBurstSize)
{
	u32 tmp;

	tmp = (hSize/pixperword)%16;

	switch(tmp)
	{
	case 0:
		*mainBurstSize = 16;
		*remainedBurstSize = 16;
		break;
	case 4:
		*mainBurstSize = 16;
		*remainedBurstSize = 4;
		break;
	case 8:
		*mainBurstSize=16;
		*remainedBurstSize = 8;
		break;
	default:
		tmp=(hSize/pixperword)%8;
		switch(tmp)
		{
		case 0:
			*mainBurstSize = 8;
			*remainedBurstSize = 8;
			break;
		case 4:
			*mainBurstSize = 8;
			*remainedBurstSize = 4;
			/* No break in the mini2440 original either: unreached for
			 * VGA and QVGA in RGB565 (640/2 and 320/2 are 0 mod 16). */
			fallthrough;
		default:
			*mainBurstSize = 4;
			tmp = (hSize/pixperword)%4;
			*remainedBurstSize = (tmp)?tmp:4;
			break;
		}
		break;
	}
}

/* calculate prescaler ratio and shift. */
static void __inline__ calc_prescaler_ratio_shift(u32 SrcSize, u32 DstSize, u32 *ratio, u32 *shift)
{
	if(SrcSize>=32*DstSize)
	{
		*ratio=32;
		*shift=5;
	}
	else if(SrcSize>=16*DstSize)
	{
		*ratio=16;
		*shift=4;
	}
	else if(SrcSize>=8*DstSize)
	{
		*ratio=8;
		*shift=3;
	}
	else if(SrcSize>=4*DstSize)
	{
		*ratio=4;
		*shift=2;
	}
	else if(SrcSize>=2*DstSize)
	{
		*ratio=2;
		*shift=1;
	}
	else
	{
		*ratio=1;
		*shift=0;
	}
}

/* update CISRCFMT only. */
static void __inline__ update_source_fmt_regs(struct s3c2440camif_dev * pdev)
{
	u32 cisrcfmt;

	cisrcfmt = (1<<31)					// ITU-R BT.601 YCbCr 8-bit mode
				|(0<<30)				// CB,Cr value offset cntrol for YCbCr
				|(pdev->srcHsize<<16)	// source image width
//				|(0<<14)				// input order is YCbYCr
//				|(1<<14)				// input order is YCrYCb
				|(2<<14)				// input order is CbYCrY
//				|(3<<14)				// input order is CrYCbY
				|(pdev->srcVsize<<0);	// source image height

	iowrite32(cisrcfmt, S3C244X_CISRCFMT);
}

/* update registers:
 *	PREVIEW path:
 *		CIPRCLRSA1 ~ CIPRCLRSA4
 *		CIPRTRGFMT
 *		CIPRCTRL
 *		CIPRSCCTRL
 *		CIPRTAREA
 */
static void __inline__ update_target_fmt_regs(struct s3c2440camif_dev * pdev)
{
	u32 ciprtrgfmt;
	u32 ciprctrl;
	u32 ciprscctrl;

	u32 mainBurstSize, remainedBurstSize;

		/* CIPRCLRSA1 ~ CIPRCLRSA2. */
		iowrite32(img_buff[0].phy_base, S3C244X_CIPRCLRSA1);
		iowrite32(img_buff[1].phy_base, S3C244X_CIPRCLRSA2);

		/* CIPRTRGFMT. */
		ciprtrgfmt = (pdev->preTargetHsize<<16)		// horizontal pixel number of target image
					 |(0<<14)						// don't mirror or rotation.
					 |(pdev->preTargetVsize<<0);	// vertical pixel number of target image
		iowrite32(ciprtrgfmt, S3C244X_CIPRTRGFMT);

		/* CIPRCTRL. */
		calc_burst_size(2, pdev->preTargetHsize, &mainBurstSize, &remainedBurstSize);
		ciprctrl = (mainBurstSize<<19)|(remainedBurstSize<<14);
		iowrite32(ciprctrl, S3C244X_CIPRCTRL);

		/* CIPRSCCTRL. */
		ciprscctrl = ioread32(S3C244X_CIPRSCCTRL);
		ciprscctrl &= 1<<15;	// clear all other info except 'preview scaler start'.
		ciprscctrl |= 0<<30;	// 16-bits RGB
		iowrite32(ciprscctrl, S3C244X_CIPRSCCTRL);	// 16-bit RGB

		/* CIPRTAREA. */
		iowrite32(pdev->preTargetHsize * pdev->preTargetVsize, S3C244X_CIPRTAREA);
}

/* update CIWDOFST only. */
static void __inline__ update_target_wnd_regs(struct s3c2440camif_dev * pdev)
{
	u32 ciwdofst;
	u32 winHorOfst, winVerOfst;

	winHorOfst = (pdev->srcHsize - pdev->wndHsize)>>1;
	winVerOfst = (pdev->srcVsize - pdev->wndVsize)>>1;

	winHorOfst &= 0xFFFFFFF8;
	winVerOfst &= 0xFFFFFFF8;
	if ((winHorOfst == 0)&&(winVerOfst == 0))
	{
		ciwdofst = 0;	// disable windows offset.
	}
	else
	{
		ciwdofst = (1<<31)				// window offset enable
					|(1<<30)			// clear the overflow ind flag of input CODEC FIFO Y
					|(winHorOfst<<16)	// windows horizontal offset
					|(1<<15)			// clear the overflow ind flag of input CODEC FIFO Cb
					|(1<<14)			// clear the overflow ind flag of input CODEC FIFO Cr
					|(1<<13)			// clear the overflow ind flag of input PREVIEW FIFO Cb
					|(1<<12)			// clear the overflow ind flag of input PREVIEW FIFO Cr
					|(winVerOfst<<0);	// window vertical offset
	}

	iowrite32(ciwdofst, S3C244X_CIWDOFST);
}

/* update registers:
 *	PREVIEW path:
 *		CIPRSCPRERATIO
 *		CIPRSCPREDST
 *		CIPRSCCTRL
 */
static void __inline__ update_target_zoom_regs(struct s3c2440camif_dev * pdev)
{
	u32 preHratio, preVratio;
	u32 Hshift, Vshift;
	u32 shfactor;
	u32 preDstWidth, preDstHeight;
	u32 Hscale, Vscale;
	u32 mainHratio, mainVratio;

	u32 ciprscpreratio;
	u32 ciprscpredst;
	u32 ciprscctrl;


		/* CIPRSCPRERATIO. */
		calc_prescaler_ratio_shift(pdev->wndHsize, pdev->preTargetHsize,
									&preHratio, &Hshift);
		calc_prescaler_ratio_shift(pdev->wndVsize, pdev->preTargetVsize,
									&preVratio, &Vshift);

		shfactor = 10 - (Hshift + Vshift);

		ciprscpreratio = (shfactor<<28)		// shift factor for preview pre-scaler
						 |(preHratio<<16)	// horizontal ratio of preview pre-scaler
						 |(preVratio<<0);	// vertical ratio of preview pre-scaler
		iowrite32(ciprscpreratio, S3C244X_CIPRSCPRERATIO);

		/* CIPRSCPREDST. */
		preDstWidth = pdev->wndHsize / preHratio;
		preDstHeight = pdev->wndVsize / preVratio;
		ciprscpredst = (preDstWidth<<16)	// destination width for preview pre-scaler
					   |(preDstHeight<<0);	// destination height for preview pre-scaler
		iowrite32(ciprscpredst, S3C244X_CIPRSCPREDST);

		/* CIPRSCCTRL. */
		Hscale = (pdev->wndHsize >= pdev->preTargetHsize)?0:1;
		Vscale = (pdev->wndVsize >= pdev->preTargetVsize)?0:1;
		mainHratio = (pdev->wndHsize<<8)/(pdev->preTargetHsize<<Hshift);
		mainVratio = (pdev->wndVsize<<8)/(pdev->preTargetVsize<<Vshift);
		ciprscctrl = ioread32(S3C244X_CIPRSCCTRL);
		ciprscctrl &= (1<<30)|(1<<15);	// keep preview image format (RGB565 or RGB24), and preview scaler start state.
		ciprscctrl |= (1<<31)	// this bit should be always 1.
					  |(Hscale<<29)		// ???, horizontal scale up/down.
					  |(Vscale<<28)		// ???, vertical scale up/down.
					  |(mainHratio<<16)	// horizontal scale ratio for preview main-scaler
					  |(mainVratio<<0);	// vertical scale ratio for preview main-scaler
		iowrite32(ciprscctrl, S3C244X_CIPRSCCTRL);
}

/* RABBITV3: polls of VSYNC after which each further poll soft-resets the camif. */
#define CAMIF_VSYNC_TRIES	20

/* update camif registers, called only when camif ready, or ISR. */
static void __inline__ update_camif_regs(struct s3c2440camif_dev * pdev)
{
	int i = 0;

	printk(KERN_ALERT"update_camif_regs");
	if (!in_irq())
	{
		while(1)	// wait until VSYNC is 'L'
		{
			printk(KERN_ALERT"update_camif_regs : while : %d\n", i);
			barrier();
			if ((ioread32(S3C244X_CICOSTATUS)&(1<<28)) == 0)
				break;
			i++;
			if (i > CAMIF_VSYNC_TRIES)
			{
				soft_reset_camif();
				printk(KERN_ALERT"update_camif_regs : soft_reset_camif\n");
			}
		}
	}

	/* WARNING: don't change the statement sort below!!! */
	update_source_fmt_regs(pdev);
	update_target_wnd_regs(pdev);
	update_target_fmt_regs(pdev);
	update_target_zoom_regs(pdev);
}

/* start image capture.
 *
 * param 'stream' means capture pictures streamly or capture only one picture.
 */
static int start_capture(struct s3c2440camif_dev * pdev, int stream)
{
	int ret;

	u32 ciwdofst;
	u32 ciprscctrl;
	u32 ciimgcpt;

	ciwdofst = ioread32(S3C244X_CIWDOFST);
	ciwdofst |= (1<<30)		// Clear the overflow indication flag of input CODEC FIFO Y
				|(1<<15)	// Clear the overflow indication flag of input CODEC FIFO Cb
				|(1<<14)	// Clear the overflow indication flag of input CODEC FIFO Cr
				|(1<<13)	// Clear the overflow indication flag of input PREVIEW FIFO Cb
				|(1<<12);	// Clear the overflow indication flag of input PREVIEW FIFO Cr
	iowrite32(ciwdofst, S3C244X_CIWDOFST);

		ciprscctrl = ioread32(S3C244X_CIPRSCCTRL);
		ciprscctrl |= 1<<15;	// preview scaler start
		iowrite32(ciprscctrl, S3C244X_CIPRSCCTRL);

		pdev->state = CAMIF_STATE_PREVIEWING;

		ciimgcpt = (1<<31)		// camera interface global capture enable
				   |(1<<29);	// capture enable for preview scaler.
		iowrite32(ciimgcpt, S3C244X_CIIMGCPT);

	ret = 0;
	if (stream == 0)
	{
		pdev->cmdcode = CAMIF_CMD_STOP;

		ret = wait_event_interruptible(pdev->cmdqueue, pdev->cmdcode == CAMIF_CMD_NONE);
	}
	return ret;
}

/* stop image capture, always called in ISR.
 *	P-path regs:
 *		CIPRSCCTRL
 *		CIPRCTRL
 *	C-path regs:
 *		CICOSCCTRL.
 *		CICOCTRL
 *	Global regs:
 *		CIIMGCPT
 */
static void stop_capture(struct s3c2440camif_dev * pdev)
{
	u32 ciprscctrl;
	u32 ciprctrl;

	u32 cicoscctrl;
	u32 cicoctrl;

	switch(pdev->state)
	{
	case CAMIF_STATE_PREVIEWING:
		/* CIPRCTRL. */
		ciprctrl = ioread32(S3C244X_CIPRCTRL);
		ciprctrl |= 1<<2;		// enable last IRQ at the end of frame capture.
		iowrite32(ciprctrl, S3C244X_CIPRCTRL);

		/* CIPRSCCTRL. */
		ciprscctrl = ioread32(S3C244X_CIPRSCCTRL);
		ciprscctrl &= ~(1<<15);		// clear preview scaler start bit.
		iowrite32(ciprscctrl, S3C244X_CIPRSCCTRL);

		/* CIIMGCPT. */
		iowrite32(0, S3C244X_CIIMGCPT);
		pdev->state = CAMIF_STATE_READY;

		break;

	case CAMIF_STATE_CODECING:
		/* CICOCTRL. */
		cicoctrl = ioread32(S3C244X_CICOCTRL);
		cicoctrl |= 1<<2;		// enable last IRQ at the end of frame capture.
		iowrite32(cicoctrl, S3C244X_CICOCTRL);

		/* CICOSCCTRL. */
		cicoscctrl = ioread32(S3C244X_CICOSCCTRL);
		cicoscctrl &= ~(1<<15);		// clear codec scaler start bit.
		iowrite32(cicoscctrl, S3C244X_CICOSCCTRL);

		/* CIIMGCPT. */
		iowrite32(0, S3C244X_CIIMGCPT);
		pdev->state = CAMIF_STATE_READY;

		break;

	}
}

/* update camera interface with the new config. */
static void update_camif_config (struct s3c2440camif_fh * fh, u32 cmdcode)
{
	struct s3c2440camif_dev	* pdev;

	pdev = fh->dev;

	switch (pdev->state)
	{
	case CAMIF_STATE_READY:
		update_camif_regs(fh->dev);		// config the regs directly.
		break;

	case CAMIF_STATE_PREVIEWING:

		/* camif is previewing image. */

		disable_irq(IRQ_S3C2440_CAM_P);		// disable cam-preview irq.

		/* source image format. */
		if (cmdcode & CAMIF_CMD_SFMT)
		{
			// ignore it, nothing to do now.
		}

		/* target image format. */
		if (cmdcode & CAMIF_CMD_TFMT)
		{
				/* change target image format only. */
				pdev->cmdcode |= CAMIF_CMD_TFMT;
		}

		/* target image window offset. */
		if (cmdcode & CAMIF_CMD_WND)
		{
			pdev->cmdcode |= CAMIF_CMD_WND;
		}

		/* target image zoomi & zoomout. */
		if (cmdcode & CAMIF_CMD_ZOOM)
		{
			pdev->cmdcode |= CAMIF_CMD_ZOOM;
		}

		/* stop previewing. */
		if (cmdcode & CAMIF_CMD_STOP)
		{
			pdev->cmdcode |= CAMIF_CMD_STOP;
		}
		enable_irq(IRQ_S3C2440_CAM_P);	// enable cam-preview irq.

		wait_event(pdev->cmdqueue, (pdev->cmdcode==CAMIF_CMD_NONE));	// wait until the ISR completes command.
		break;

	case CAMIF_STATE_CODECING:

		/* camif is previewing image. */

		disable_irq(IRQ_S3C2440_CAM_C);		// disable cam-codec irq.

		/* source image format. */
		if (cmdcode & CAMIF_CMD_SFMT)
		{
			// ignore it, nothing to do now.
		}

		/* target image format. */
		if (cmdcode & CAMIF_CMD_TFMT)
		{
				/* change target image format only. */
				pdev->cmdcode |= CAMIF_CMD_TFMT;
		}

		/* target image window offset. */
		if (cmdcode & CAMIF_CMD_WND)
		{
			pdev->cmdcode |= CAMIF_CMD_WND;
		}

		/* target image zoomi & zoomout. */
		if (cmdcode & CAMIF_CMD_ZOOM)
		{
			pdev->cmdcode |= CAMIF_CMD_ZOOM;
		}

		/* stop previewing. */
		if (cmdcode & CAMIF_CMD_STOP)
		{
			pdev->cmdcode |= CAMIF_CMD_STOP;
		}
		enable_irq(IRQ_S3C2440_CAM_C);	// enable cam-codec irq.
		wait_event(pdev->cmdqueue, (pdev->cmdcode==CAMIF_CMD_NONE));	// wait until the ISR completes command.
		break;

	default:
		break;
	}
}

/* config camif when master-open camera.*/
static void init_camif_config(struct s3c2440camif_fh * fh)
{
	struct s3c2440camif_dev	* pdev;
	int width = 0, height = 0;

	pdev = fh->dev;

	pdev->input = 0;	// FIXME, the default input image format, see inputs[] for detail.

	if (pdev->resolution == CAMIF_RES_QVGA)
	{
		width = CAMIF_QVGA_WIDTH;
		height = CAMIF_QVGA_HEIGHT;
	}
	else if (pdev->resolution == CAMIF_RES_VGA)
	{
		width = CAMIF_VGA_WIDTH;
		height = CAMIF_VGA_HEIGHT;
	}

	/* the source image size (input from external camera). */
	pdev->srcHsize = width;
	pdev->srcVsize = height;

	/* the windowed image size. */
	pdev->wndHsize = width;
	pdev->wndVsize = height;

	/* codec-path target(output) image size. */
	pdev->coTargetHsize = pdev->wndHsize;
	pdev->coTargetVsize = pdev->wndVsize;

	/* preview-path target(preview) image size. */
	pdev->preTargetHsize = width;
	pdev->preTargetVsize = height;

	update_camif_config(fh, CAMIF_CMD_STOP);
}

static void __inline__ invalid_image_buffer(void)
{
	img_buff[0].state = CAMIF_BUFF_INVALID;
	img_buff[1].state = CAMIF_BUFF_INVALID;
}

/* init image buffer (only once, at boot). */
static int __inline__ init_image_buffer(void)
{
	int size1, size2, i;
	unsigned long size;
	unsigned int order;

	printk(KERN_ALERT"init_image_buffer start\n");

	/* already allocated? */
	if ((img_buff[0].virt_base != (unsigned long)NULL && img_buff[1].virt_base == (unsigned long)NULL)
			|| img_buff[0].order != 0)
	{
		return 0;
	}

	/* size1 is the max image size of codec path. */
	size1 = MAX_C_WIDTH * MAX_C_HEIGHT * 16 / 8;

	/* size2 is the max image size of preview path. */
	size2 = MAX_P_WIDTH * MAX_P_HEIGHT * 16 / 8;

	size = (size1 > size2)?size1:size2;

	/* Coherent, not __get_free_pages and a physical address worked out by
	 * hand: the ARM920T does not keep its cache coherent with DMA by
	 * itself (the USB hub's stale reads under 6.1, 2026-11-25). */
	order = get_order(size);
	img_buff_size = size;
	for (i = 0; i < CAMIF_NR_BUFFERS; i++) {
		dma_addr_t dma;
		void *v = dma_alloc_coherent(&camif_pdev->dev, size, &dma, GFP_KERNEL);
		if (!v) {
			while (i--)
				dma_free_coherent(&camif_pdev->dev, size, (void *)img_buff[i].virt_base,
						  img_buff[i].phy_base);
			return -ENOMEM;
		}
		img_buff[i].order = order;
		img_buff[i].virt_base = (unsigned long)v;
		img_buff[i].phy_base = dma;
	}

	invalid_image_buffer();

	return 0;
}

/*
 * ISR: service for C-path interrupt.
 */
static irqreturn_t on_camif_irq_c(int irq, void * dev)
{
	u32 cicostatus;

	u32 frame;
	struct s3c2440camif_dev * pdev;

	cicostatus = ioread32(S3C244X_CICOSTATUS);
	if ((cicostatus & (1<<21))== 0)
	{
		return IRQ_RETVAL(IRQ_NONE);
	}

	pdev = (struct s3c2440camif_dev *)dev;

	/* valid img_buff[x] just DMAed. */
	frame = (cicostatus&(3<<26))>>26;
	frame = (frame+CAMIF_NR_BUFFERS-1)%CAMIF_NR_BUFFERS;

	if (pdev->cmdcode & CAMIF_CMD_STOP)
	{
		stop_capture(pdev);

		pdev->state = CAMIF_STATE_READY;
	}
	else
	{
		if (pdev->cmdcode & CAMIF_CMD_C2P)
		{
			camif_c2p(pdev);
		}

		if (pdev->cmdcode & CAMIF_CMD_WND)
		{
			update_target_wnd_regs(pdev);
		}

		if (pdev->cmdcode & CAMIF_CMD_TFMT)
		{
			update_target_fmt_regs(pdev);
		}

		if (pdev->cmdcode & CAMIF_CMD_ZOOM)
		{
			update_target_zoom_regs(pdev);
		}

		invalid_image_buffer();
	}
	pdev->cmdcode = CAMIF_CMD_NONE;
	wake_up(&pdev->cmdqueue);

	return IRQ_RETVAL(IRQ_HANDLED);
}

/*
 * ISR: service for P-path interrupt.
 */
static irqreturn_t on_camif_irq_p(int irq, void * dev)
{
	u32 ciprstatus;

	u32 frame;
	struct s3c2440camif_dev * pdev;

	ciprstatus = ioread32(S3C244X_CIPRSTATUS);
	if ((ciprstatus & (1<<21))== 0)
	{
		return IRQ_RETVAL(IRQ_NONE);
	}

	pdev = (struct s3c2440camif_dev *)dev;

	/* valid img_buff[x] just DMAed. */
	frame = (ciprstatus&(3<<26))>>26;
	frame = (frame+CAMIF_NR_BUFFERS-1)%CAMIF_NR_BUFFERS;

		img_buff[frame].state = CAMIF_BUFF_RGB565;

	if (pdev->cmdcode & CAMIF_CMD_STOP)
	{
		stop_capture(pdev);

		pdev->state = CAMIF_STATE_READY;
	}
	else
	{
		if (pdev->cmdcode & CAMIF_CMD_P2C)
		{
			camif_c2p(pdev);
		}

		if (pdev->cmdcode & CAMIF_CMD_WND)
		{
			update_target_wnd_regs(pdev);
		}

		if (pdev->cmdcode & CAMIF_CMD_TFMT)
		{
			update_target_fmt_regs(pdev);
		}

		if (pdev->cmdcode & CAMIF_CMD_ZOOM)
		{
			update_target_zoom_regs(pdev);
		}

		invalid_image_buffer();
	}
	pdev->cmdcode = CAMIF_CMD_NONE;
	wake_up(&pdev->cmdqueue);

	return IRQ_RETVAL(IRQ_HANDLED);
}

/*
 * camif_open()
 */
static int camif_open(struct inode *inode, struct file *file)
{
	struct s3c2440camif_dev *pdev;
	struct s3c2440camif_fh *fh;

	int ret;

	printk(KERN_ALERT"OV7675 camif_open\n");
	if (!has_ov7675) {
		printk(KERN_ERR"camif_open error !has_ov7675\n");
		return -ENODEV;
	}
	pdev = &camera;


	fh = kzalloc(sizeof(*fh),GFP_KERNEL); // alloc memory for filehandle
	if (NULL == fh)
	{
		printk(KERN_ERR"camif_open error NULL == fh\n");
		return -ENOMEM;
	}
	fh->dev = pdev;

		pdev->state = CAMIF_STATE_READY;

		init_camif_config(fh);

		if (CAMIF_STATE_FREE == pdev->state)
		{
			printk(KERN_ERR"camif_open error CAMIF_STATE_FREE == pdev->state. return ETIMEDOUT\n");
			soft_reset_camif();
			return -ETIMEDOUT;
		}

		ret = request_irq(IRQ_S3C2440_CAM_C, on_camif_irq_c, 0, "CAM_C", pdev);	// setup ISRs
		if (ret < 0)
		{
			printk(KERN_ERR"camif_open error : goto error2\n");
			goto error2;
		}

		ret = request_irq(IRQ_S3C2440_CAM_P, on_camif_irq_p, 0, "CAM_P", pdev);
		if (ret < 0)
		{
			printk(KERN_ERR"camif_open error : goto error3\n");
			goto error3;
		}

		clk_prepare_enable(pdev->clk);		// and enable camif clock.

		soft_reset_camif();

	file->private_data = fh;
	fh->dev  = pdev;
	update_camif_config(fh, 0);
	return 0;

error3:
	free_irq(IRQ_S3C2440_CAM_C, pdev);
error2:
	kfree(fh);
	return ret;
}

/*
 * camif_read()
 */
static ssize_t camif_read(struct file *file, char __user *data, size_t count, loff_t *ppos)
{
	int i;
	struct s3c2440camif_fh * fh;
	struct s3c2440camif_dev * pdev;

	fh = file->private_data;
	pdev = fh->dev;


	if (start_capture(pdev, 0) != 0)
	{
		return -ERESTARTSYS;
	}

	disable_irq(IRQ_S3C2440_CAM_C);
	disable_irq(IRQ_S3C2440_CAM_P);
	for (i = 0; i < CAMIF_NR_BUFFERS; i++)
	{
		if (img_buff[i].state != CAMIF_BUFF_INVALID)
		{
			/* No more than a buffer holds: the original copied whatever
			 * was asked, past the image into the kernel's memory. */
			if (count > img_buff_size)
				count = img_buff_size;
			if (copy_to_user(data, (void *)img_buff[i].virt_base, count))
				count = 0;
			img_buff[i].state = CAMIF_BUFF_INVALID;
			break;
		}
	}
	enable_irq(IRQ_S3C2440_CAM_P);
	enable_irq(IRQ_S3C2440_CAM_C);


	return count;
}

/*
 * camif_release()
 */
static int camif_release(struct inode *inode, struct file *file)
{
	struct s3c2440camif_fh	* fh;
	struct s3c2440camif_dev * pdev;

	fh = file->private_data;
	pdev = fh->dev;


		clk_disable_unprepare(pdev->clk);				// stop camif clock

		free_irq(IRQ_S3C2440_CAM_P, pdev);	// free camif IRQs

		free_irq(IRQ_S3C2440_CAM_C, pdev);

	return 0;
}

/*
 * soft SCCB bus (sccb.c), the SCCB master side protocol.
 */
static DEFINE_SEMAPHORE(bus_lock);

static void __inline__ sccb_start(void)
{
	CFG_WRITE(SIO_D);

	Low(SIO_D);
	WAIT_STABLE();
}

static void __inline__ sccb_write_byte(u8 data)
{
	int i;

	CFG_WRITE(SIO_D);
	WAIT_STABLE();

	/* write 8-bits octet. */
	for (i=0;i<8;i++)
	{
		Low(SIO_C);
		WAIT_STABLE();

		if (data & 0x80)
		{
			High(SIO_D);
		}
		else
		{
			Low(SIO_D);
		}
		data = data<<1;
		WAIT_CYCLE();

		High(SIO_C);
		WAIT_CYCLE();
	}

	/* write byte done, wait the Don't care bit now. */
	{
		Low(SIO_C);
		High(SIO_D);
		CFG_READ(SIO_D);
		WAIT_CYCLE();

		High(SIO_C);
		WAIT_CYCLE();
	}
}

static u8 __inline__ sccb_read_byte(void)
{
	int i;
	u8 data;

	CFG_READ(SIO_D);
	WAIT_STABLE();

	Low(SIO_C);
	WAIT_CYCLE();

	data = 0;
	for (i=0;i<8;i++)
	{
		High(SIO_C);
		WAIT_STABLE();

		data = data<<1;
		data |= State(SIO_D)?1:0;
		WAIT_CYCLE();

		Low(SIO_C);
		WAIT_CYCLE();
	}

	/* read byte down, write the NA bit now.*/
	{
		CFG_WRITE(SIO_D);
		High(SIO_D);
		WAIT_CYCLE();

		High(SIO_C);
		WAIT_CYCLE();
	}

	return data;
}

static void __inline__ sccb_stop(void)
{
	Low(SIO_C);
	WAIT_STABLE();

	CFG_WRITE(SIO_D);
	Low(SIO_D);
	WAIT_CYCLE();

	High(SIO_C);
	WAIT_STABLE();

	High(SIO_D);
	WAIT_CYCLE();

	CFG_READ(SIO_D);
}

void sccb_write(u8 IdAddr, u8 SubAddr, u8 data)
{
	down(&bus_lock);
	sccb_start();
	sccb_write_byte(IdAddr);
	sccb_write_byte(SubAddr);
	sccb_write_byte(data);
	sccb_stop();
	up (&bus_lock);
}

#define SCCB_READ	0x01	/* the read bit of the device address */

u8 sccb_read(u8 IdAddr, u8 SubAddr)
{
	u8 data;

	down(&bus_lock);
	sccb_start();
	sccb_write_byte(IdAddr);
	sccb_write_byte(SubAddr);
	sccb_stop();

	sccb_start();
	sccb_write_byte(IdAddr|SCCB_READ);
	data = sccb_read_byte();
	sccb_stop();
	up(&bus_lock);

	return data;
}

static int __inline__ sccb_init(void)
{
	CFG_WRITE(SIO_C);
	CFG_WRITE(SIO_D);

	High(SIO_C);
	High(SIO_D);
	WAIT_STABLE();

	return 0;
}

static void __inline__ sccb_cleanup(void)
{
	CFG_READ(SIO_C);
	CFG_READ(SIO_D);
}

/*
 * OV7675 sensor (s3c2440_ov7675.c).
 */
#define OV7675_SCCB_ADDR	0x42
#define OV7675_MANUFACT_ID	0x7FA2
#define OV7675_PRODUCT_ID	0x7673

/* OmniVision ID registers. */
#define OV7675_REG_PID		0x0a	/* product ID, high byte */
#define OV7675_REG_VER		0x0b	/* product ID, low byte */
#define OV7675_REG_MIDH		0x1c	/* manufacturer ID, high byte */
#define OV7675_REG_MIDL		0x1d	/* manufacturer ID, low byte */

/* The other registers the tables below write, with the datasheet's names
 * (OV7675/OV7175 datasheet v2.0, device control registers). Its register
 * table shifts the names of 0x92-0x95 down a row (0x92 "DM_LNH", 0x93
 * "LCC6", ...); the descriptions there and the lens correction section give
 * 0x92/0x93 as the dummy line LSBs/MSBs and 0x94/0x95 as LCC6/LCC7. */
#define OV7675_REG_GAIN		0x00
#define OV7675_REG_BLUE		0x01
#define OV7675_REG_RED		0x02
#define OV7675_REG_VREF		0x03
#define OV7675_REG_COM1		0x04
#define OV7675_REG_COM2		0x09
#define OV7675_REG_COM3		0x0c
#define OV7675_REG_COM4		0x0d
#define OV7675_REG_COM6		0x0f
#define OV7675_REG_AECH		0x10
#define OV7675_REG_CLKRC	0x11
#define OV7675_REG_COM7		0x12
#define OV7675_REG_COM8		0x13
#define OV7675_REG_COM9		0x14
#define OV7675_REG_COM10	0x15
#define OV7675_REG_HSTART	0x17
#define OV7675_REG_HSTOP	0x18
#define OV7675_REG_VSTART	0x19
#define OV7675_REG_VSTOP	0x1a
#define OV7675_REG_MVFP		0x1e
#define OV7675_REG_AEW		0x24
#define OV7675_REG_AEB		0x25
#define OV7675_REG_VPT		0x26
#define OV7675_REG_EXHCH	0x2a
#define OV7675_REG_EXHCL	0x2b
#define OV7675_REG_HREF		0x32
#define OV7675_REG_TSLB		0x3a
#define OV7675_REG_COM11	0x3b
#define OV7675_REG_COM12	0x3c
#define OV7675_REG_COM13	0x3d
#define OV7675_REG_COM14	0x3e
#define OV7675_REG_EDGE		0x3f
#define OV7675_REG_COM16	0x41
#define OV7675_REG_DNSTH	0x4c
#define OV7675_REG_MTX1		0x4f
#define OV7675_REG_MTX2		0x50
#define OV7675_REG_MTX3		0x51
#define OV7675_REG_MTX4		0x52
#define OV7675_REG_MTX5		0x53
#define OV7675_REG_MTX6		0x54
#define OV7675_REG_BRIGHT	0x55
#define OV7675_REG_CONTRAS	0x56
#define OV7675_REG_CONTRAS_CENTER	0x57
#define OV7675_REG_MTXS		0x58
#define OV7675_REG_LCC1		0x62
#define OV7675_REG_LCC2		0x63
#define OV7675_REG_LCC3		0x64
#define OV7675_REG_LCC4		0x65
#define OV7675_REG_LCC5		0x66
#define OV7675_REG_GFIX		0x69
#define OV7675_REG_GREEN	0x6a
#define OV7675_REG_DBLV		0x6b
#define OV7675_REG_AWBCTR3	0x6c
#define OV7675_REG_AWBCTR2	0x6d
#define OV7675_REG_AWBCTR1	0x6e
#define OV7675_REG_AWBCTR0	0x6f
#define OV7675_REG_SCALING_XSC	0x70
#define OV7675_REG_SCALING_YSC	0x71
#define OV7675_REG_REG74	0x74
#define OV7675_REG_REG75	0x75
#define OV7675_REG_REG76	0x76
#define OV7675_REG_REG77	0x77
#define OV7675_REG_SLOP		0x7a
#define OV7675_REG_GAM1		0x7b
#define OV7675_REG_GAM2		0x7c
#define OV7675_REG_GAM3		0x7d
#define OV7675_REG_GAM4		0x7e
#define OV7675_REG_GAM5		0x7f
#define OV7675_REG_GAM6		0x80
#define OV7675_REG_GAM7		0x81
#define OV7675_REG_GAM8		0x82
#define OV7675_REG_GAM9		0x83
#define OV7675_REG_GAM10	0x84
#define OV7675_REG_GAM11	0x85
#define OV7675_REG_GAM12	0x86
#define OV7675_REG_GAM13	0x87
#define OV7675_REG_GAM14	0x88
#define OV7675_REG_GAM15	0x89
#define OV7675_REG_DM_LNL	0x92
#define OV7675_REG_DM_LNH	0x93
#define OV7675_REG_LCC6		0x94
#define OV7675_REG_LCC7		0x95
#define OV7675_REG_BD50ST	0x9d
#define OV7675_REG_BD60ST	0x9e
#define OV7675_REG_NT_CTRL	0xa4
#define OV7675_REG_BD50MAX	0xa5
#define OV7675_REG_BD60MAX	0xab
#define OV7675_REG_SATCTR	0xc9

/* A register-table entry with this subaddress is a delay of value ms. */
#define OV7675_REG_DELAY	0xff

struct ov7675_reg
{
	u8 subaddr;
	u8 value;
};

/*
 * The register tables, written in this order. The values are sensor
 * tuning without a documented meaning and stay numeric. The subaddresses
 * that stay numeric are the ones the datasheet lists only as "debug mode" or
 * "not used" (0x0e, 0x16, 0x21, 0x22, 0x29, 0x33-0x39, 0x43-0x4b, 0x4d,
 * 0x4e, 0x59-0x5e, 0x72, 0x73, 0x78, 0x79, 0x8d-0x91, 0x96-0x9c, 0x9f-0xa2,
 * 0xa6-0xaa, 0xb0-0xb9, 0xc1, 0xc8, 0xd2, 0xe6).
 */

/* OV7675 initialisation table for VGA (640*480) */
struct ov7675_reg regs_vga[] = {
	{OV7675_REG_COM7, 0x80}, {OV7675_REG_COM2, 0x10}, {0xc1, 0x7f}, {OV7675_REG_CLKRC, 0x80},
	{OV7675_REG_TSLB, 0x0c}, {OV7675_REG_COM13, 0xc0}, {OV7675_REG_COM7, 0x00}, {OV7675_REG_COM10, 0x40},
	{OV7675_REG_HSTART, 0x13}, {OV7675_REG_HSTOP, 0x01}, {OV7675_REG_HREF, 0xbf}, {OV7675_REG_VSTART, 0x02},
	{OV7675_REG_VSTOP, 0x7a}, {OV7675_REG_VREF, 0x0a}, {OV7675_REG_COM3, 0x00}, {OV7675_REG_COM14, 0x00},
	{OV7675_REG_SCALING_XSC, 0x3a}, {OV7675_REG_SCALING_YSC, 0x35}, {0x72, 0x11}, {0x73, 0xf0},
	{0xa2, 0x02}, {OV7675_REG_SLOP, 0x20}, {OV7675_REG_GAM1, 0x03}, {OV7675_REG_GAM2, 0x0a},
	{OV7675_REG_GAM3, 0x1a}, {OV7675_REG_GAM4, 0x3f}, {OV7675_REG_GAM5, 0x4e}, {OV7675_REG_GAM6, 0x5b},
	{OV7675_REG_GAM7, 0x68}, {OV7675_REG_GAM8, 0x75}, {OV7675_REG_GAM9, 0x7f}, {OV7675_REG_GAM10, 0x89},
	{OV7675_REG_GAM11, 0x9a}, {OV7675_REG_GAM12, 0xa6}, {OV7675_REG_GAM13, 0xbd}, {OV7675_REG_GAM14, 0xd3},
	{OV7675_REG_GAM15, 0xe8}, {OV7675_REG_COM8, 0xe0}, {OV7675_REG_GAIN, 0x00}, {OV7675_REG_AECH, 0x00},
	{OV7675_REG_COM4, 0x40}, {OV7675_REG_COM9, 0x28}, {OV7675_REG_BD50MAX, 0x02}, {OV7675_REG_BD60MAX, 0x02},
	{OV7675_REG_AEW, 0x68}, {OV7675_REG_AEB, 0x58}, {OV7675_REG_VPT, 0xc2}, {0x9f, 0x78},
	{0xa0, 0x68}, {0xa1, 0x03}, {0xa6, 0xd8}, {0xa7, 0xd8},
	{0xa8, 0xf0}, {0xa9, 0x90}, {0xaa, 0x14}, {OV7675_REG_COM8, 0xe5},
	{0x0e, 0x61}, {OV7675_REG_COM6, 0x4b}, {0x16, 0x02}, {OV7675_REG_MVFP, 0x37},
	{0x21, 0x02}, {0x22, 0x91}, {0x29, 0x07}, {0x33, 0x0b},
	{0x35, 0x0b}, {0x37, 0x1d}, {0x38, 0x71}, {0x39, 0x2a},
	{OV7675_REG_COM12, 0x78}, {0x4d, 0x40}, {0x4e, 0x20}, {OV7675_REG_GFIX, 0x00},
	{OV7675_REG_DBLV, 0x0a}, {OV7675_REG_REG74, 0x10}, {0x8d, 0x4f}, {0x8e, 0x00},
	{0x8f, 0x00}, {0x90, 0x00}, {0x91, 0x00}, {0x96, 0x00},
	{0x9a, 0x80}, {0xb0, 0x84}, {0xb1, 0x0c}, {0xb2, 0x0e},
	{0xb3, 0x82}, {0xb8, 0x0a}, {0x43, 0x0a}, {0x44, 0xf2},
	{0x45, 0x39}, {0x46, 0x62}, {0x47, 0x3d}, {0x48, 0x55},
	{0x59, 0x83}, {0x5a, 0x0d}, {0x5b, 0xcd}, {0x5c, 0x8c},
	{0x5d, 0x77}, {0x5e, 0x16}, {OV7675_REG_AWBCTR3, 0x0a}, {OV7675_REG_AWBCTR2, 0x65},
	{OV7675_REG_AWBCTR1, 0x11}, {OV7675_REG_AWBCTR0, 0x9e}, {OV7675_REG_GREEN, 0x40}, {OV7675_REG_BLUE, 0x56},
	{OV7675_REG_RED, 0x44}, {OV7675_REG_COM8, 0xe7}, {OV7675_REG_MTX1, 0x88}, {OV7675_REG_MTX2, 0x8b},
	{OV7675_REG_MTX3, 0x04}, {OV7675_REG_MTX4, 0x11}, {OV7675_REG_MTX5, 0x8c}, {OV7675_REG_MTX6, 0x9d},
	{OV7675_REG_BRIGHT, 0x00}, {OV7675_REG_CONTRAS, 0x40}, {OV7675_REG_CONTRAS_CENTER, 0x80}, {OV7675_REG_MTXS, 0x9a},
	{OV7675_REG_COM16, 0x08}, {OV7675_REG_EDGE, 0x00}, {OV7675_REG_REG75, 0x04}, {OV7675_REG_REG76, 0x60},
	{OV7675_REG_DNSTH, 0x00}, {OV7675_REG_REG77, 0x01}, {OV7675_REG_COM13, 0xc2}, {0x4b, 0x09},
	{OV7675_REG_SATCTR, 0x30}, {OV7675_REG_COM16, 0x38}, {OV7675_REG_CONTRAS, 0x40}, {0x34, 0x11},
	{OV7675_REG_COM11, 0x12}, {OV7675_REG_NT_CTRL, 0x88}, {0x96, 0x00}, {0x97, 0x30},
	{0x98, 0x20}, {0x99, 0x30}, {0x9a, 0x84}, {0x9b, 0x29},
	{0x9c, 0x03}, {OV7675_REG_BD50ST, 0x99}, {OV7675_REG_BD60ST, 0x7f}, {0x78, 0x04},
	{0x79, 0x01}, {0xc8, 0xf0}, {0x79, 0x0f}, {0xc8, 0x00},
	{0x79, 0x10}, {0xc8, 0x7e}, {0x79, 0x0a}, {0xc8, 0x80},
	{0x79, 0x0b}, {0xc8, 0x01}, {0x79, 0x0c}, {0xc8, 0x0f},
	{0x79, 0x0d}, {0xc8, 0x20}, {0x79, 0x09}, {0xc8, 0x80},
	{0x79, 0x02}, {0xc8, 0xc0}, {0x79, 0x03}, {0xc8, 0x40},
	{0x79, 0x05}, {0xc8, 0x30}, {0x79, 0x26}, {OV7675_REG_LCC1, 0x00},
	{OV7675_REG_LCC2, 0x00}, {OV7675_REG_LCC3, 0x06}, {OV7675_REG_LCC4, 0x00}, {OV7675_REG_LCC5, 0x05},
	{OV7675_REG_LCC6, 0x05}, {OV7675_REG_LCC7, 0x09}, {OV7675_REG_EXHCH, 0x10}, {OV7675_REG_EXHCL, 0xc2},
	{OV7675_REG_COM10, 0x00}, {OV7675_REG_TSLB, 0x0c}, {OV7675_REG_COM13, 0xc3}, {OV7675_REG_VSTART, 0x03},
	{OV7675_REG_VSTOP, 0x7b}, {OV7675_REG_EXHCH, 0x00}, {OV7675_REG_EXHCL, 0x00}, {OV7675_REG_HSTOP, 0x01},
	{OV7675_REG_LCC5, 0x05}, {OV7675_REG_LCC1, 0x10}, {OV7675_REG_LCC2, 0x0b}, {OV7675_REG_LCC4, 0x07},
	{OV7675_REG_LCC3, 0x0f}, {OV7675_REG_LCC6, 0x0e}, {OV7675_REG_LCC7, 0x10}, {OV7675_REG_MTX1, 0x87},
	{OV7675_REG_MTX2, 0x68}, {OV7675_REG_MTX3, 0x1e}, {OV7675_REG_MTX4, 0x15}, {OV7675_REG_MTX5, 0x7c},
	{OV7675_REG_MTX6, 0x91}, {OV7675_REG_MTXS, 0x1e}, {OV7675_REG_COM16, 0x38}, {OV7675_REG_REG76, 0xe0},
	{OV7675_REG_AEW, 0x40}, {OV7675_REG_AEB, 0x38}, {OV7675_REG_VPT, 0x91}, {OV7675_REG_SLOP, 0x09},
	{OV7675_REG_GAM1, 0x0c}, {OV7675_REG_GAM2, 0x16}, {OV7675_REG_GAM3, 0x28}, {OV7675_REG_GAM4, 0x48},
	{OV7675_REG_GAM5, 0x57}, {OV7675_REG_GAM6, 0x64}, {OV7675_REG_GAM7, 0x71}, {OV7675_REG_GAM8, 0x7e},
	{OV7675_REG_GAM9, 0x89}, {OV7675_REG_GAM10, 0x94}, {OV7675_REG_GAM11, 0xa8}, {OV7675_REG_GAM12, 0xba},
	{OV7675_REG_GAM13, 0xd7}, {OV7675_REG_GAM14, 0xec}, {OV7675_REG_GAM15, 0xf9}, {OV7675_REG_COM2, 0x00},
};

/* OV7675 initialisation table for QVGA (320*240) */
struct ov7675_reg regs_qvga[] = {
	{OV7675_REG_COM7, 0x80}, {OV7675_REG_COM2, 0x10}, {0xc1, 0x7f}, {OV7675_REG_CLKRC, 0x80},
	{OV7675_REG_TSLB, 0x0c}, {OV7675_REG_COM13, 0xc0}, {OV7675_REG_COM7, 0x00}, {OV7675_REG_COM10, 0x40},
	{OV7675_REG_HSTART, 0x13}, {OV7675_REG_HSTOP, 0x01}, {OV7675_REG_HREF, 0xbf}, {OV7675_REG_VSTART, 0x02},
	{OV7675_REG_VSTOP, 0x7a}, {OV7675_REG_VREF, 0x0a}, {OV7675_REG_COM3, 0x00}, {OV7675_REG_COM14, 0x00},
	{OV7675_REG_SCALING_XSC, 0x3a}, {OV7675_REG_SCALING_YSC, 0x35}, {0x72, 0x11}, {0x73, 0xf0},
	{0xa2, 0x02}, {OV7675_REG_SLOP, 0x20}, {OV7675_REG_GAM1, 0x03}, {OV7675_REG_GAM2, 0x0a},
	{OV7675_REG_GAM3, 0x1a}, {OV7675_REG_GAM4, 0x3f}, {OV7675_REG_GAM5, 0x4e}, {OV7675_REG_GAM6, 0x5b},
	{OV7675_REG_GAM7, 0x68}, {OV7675_REG_GAM8, 0x75}, {OV7675_REG_GAM9, 0x7f}, {OV7675_REG_GAM10, 0x89},
	{OV7675_REG_GAM11, 0x9a}, {OV7675_REG_GAM12, 0xa6}, {OV7675_REG_GAM13, 0xbd}, {OV7675_REG_GAM14, 0xd3},
	{OV7675_REG_GAM15, 0xe8}, {OV7675_REG_COM8, 0xe0}, {OV7675_REG_GAIN, 0x00}, {OV7675_REG_AECH, 0x00},
	{OV7675_REG_COM4, 0x40}, {OV7675_REG_COM9, 0x28}, {OV7675_REG_BD50MAX, 0x02}, {OV7675_REG_BD60MAX, 0x02},
	{OV7675_REG_AEW, 0x68}, {OV7675_REG_AEB, 0x58}, {OV7675_REG_VPT, 0xc2}, {0x9f, 0x78},
	{0xa0, 0x68}, {0xa1, 0x03}, {0xa6, 0xd8}, {0xa7, 0xd8},
	{0xa8, 0xf0}, {0xa9, 0x90}, {0xaa, 0x14}, {OV7675_REG_COM8, 0xe5},
	{0x0e, 0x61}, {OV7675_REG_COM6, 0x4b}, {0x16, 0x02}, {OV7675_REG_MVFP, 0x37},
	{0x21, 0x02}, {0x22, 0x91}, {0x29, 0x07}, {0x33, 0x0b},
	{0x35, 0x0b}, {0x37, 0x1d}, {0x38, 0x71}, {0x39, 0x2a},
	{OV7675_REG_COM12, 0x78}, {0x4d, 0x40}, {0x4e, 0x20}, {OV7675_REG_GFIX, 0x00},
	{OV7675_REG_DBLV, 0x0a}, {OV7675_REG_REG74, 0x10}, {0x8d, 0x4f}, {0x8e, 0x00},
	{0x8f, 0x00}, {0x90, 0x00}, {0x91, 0x00}, {0x96, 0x00},
	{0x9a, 0x80}, {0xb0, 0x84}, {0xb1, 0x0c}, {0xb2, 0x0e},
	{0xb3, 0x82}, {0xb8, 0x0a}, {0x43, 0x0a}, {0x44, 0xf2},
	{0x45, 0x39}, {0x46, 0x62}, {0x47, 0x3d}, {0x48, 0x55},
	{0x59, 0x83}, {0x5a, 0x0d}, {0x5b, 0xcd}, {0x5c, 0x8c},
	{0x5d, 0x77}, {0x5e, 0x16}, {OV7675_REG_AWBCTR3, 0x0a}, {OV7675_REG_AWBCTR2, 0x65},
	{OV7675_REG_AWBCTR1, 0x11}, {OV7675_REG_AWBCTR0, 0x9e}, {OV7675_REG_GREEN, 0x40}, {OV7675_REG_BLUE, 0x56},
	{OV7675_REG_RED, 0x44}, {OV7675_REG_COM8, 0xe7}, {OV7675_REG_MTX1, 0x88}, {OV7675_REG_MTX2, 0x8b},
	{OV7675_REG_MTX3, 0x04}, {OV7675_REG_MTX4, 0x11}, {OV7675_REG_MTX5, 0x8c}, {OV7675_REG_MTX6, 0x9d},
	{OV7675_REG_BRIGHT, 0x00}, {OV7675_REG_CONTRAS, 0x40}, {OV7675_REG_CONTRAS_CENTER, 0x80}, {OV7675_REG_MTXS, 0x9a},
	{OV7675_REG_COM16, 0x08}, {OV7675_REG_EDGE, 0x00}, {OV7675_REG_REG75, 0x04}, {OV7675_REG_REG76, 0x60},
	{OV7675_REG_DNSTH, 0x00}, {OV7675_REG_REG77, 0x01}, {OV7675_REG_COM13, 0xc2}, {0x4b, 0x09},
	{OV7675_REG_SATCTR, 0x30}, {OV7675_REG_COM16, 0x38}, {OV7675_REG_CONTRAS, 0x40}, {0x34, 0x11},
	{OV7675_REG_COM11, 0x12}, {OV7675_REG_NT_CTRL, 0x88}, {0x96, 0x00}, {0x97, 0x30},
	{0x98, 0x20}, {0x99, 0x30}, {0x9a, 0x84}, {0x9b, 0x29},
	{0x9c, 0x03}, {OV7675_REG_BD50ST, 0x99}, {OV7675_REG_BD60ST, 0x7f}, {0x78, 0x04},
	{0x79, 0x01}, {0xc8, 0xf0}, {0x79, 0x0f}, {0xc8, 0x00},
	{0x79, 0x10}, {0xc8, 0x7e}, {0x79, 0x0a}, {0xc8, 0x80},
	{0x79, 0x0b}, {0xc8, 0x01}, {0x79, 0x0c}, {0xc8, 0x0f},
	{0x79, 0x0d}, {0xc8, 0x20}, {0x79, 0x09}, {0xc8, 0x80},
	{0x79, 0x02}, {0xc8, 0xc0}, {0x79, 0x03}, {0xc8, 0x40},
	{0x79, 0x05}, {0xc8, 0x30}, {0x79, 0x26}, {OV7675_REG_LCC1, 0x00},
	{OV7675_REG_LCC2, 0x00}, {OV7675_REG_LCC3, 0x06}, {OV7675_REG_LCC4, 0x00}, {OV7675_REG_LCC5, 0x05},
	{OV7675_REG_LCC6, 0x05}, {OV7675_REG_LCC7, 0x09}, {OV7675_REG_EXHCH, 0x10}, {OV7675_REG_EXHCL, 0xc2},
	{OV7675_REG_COM10, 0x00}, {OV7675_REG_TSLB, 0x0c}, {OV7675_REG_COM13, 0xc3}, {OV7675_REG_VSTART, 0x03},
	{OV7675_REG_VSTOP, 0x7b}, {OV7675_REG_EXHCH, 0x00}, {OV7675_REG_EXHCL, 0x00}, {OV7675_REG_HSTOP, 0x01},
	{OV7675_REG_DM_LNL, 0x88}, {OV7675_REG_DM_LNH, 0x00}, {0xb9, 0x30}, {OV7675_REG_VSTART, 0x02},
	{OV7675_REG_VSTOP, 0x3e}, {OV7675_REG_HSTART, 0x13}, {OV7675_REG_HSTOP, 0x3b}, {OV7675_REG_VREF, 0x0a},
	{0xe6, 0x05}, {0xd2, 0x1c}, {OV7675_REG_LCC5, 0x05}, {OV7675_REG_LCC1, 0x10},
	{OV7675_REG_LCC2, 0x0b}, {OV7675_REG_LCC4, 0x07}, {OV7675_REG_LCC3, 0x0f}, {OV7675_REG_LCC6, 0x0e},
	{OV7675_REG_LCC7, 0x10}, {OV7675_REG_MTX1, 0x87}, {OV7675_REG_MTX2, 0x68}, {OV7675_REG_MTX3, 0x1e},
	{OV7675_REG_MTX4, 0x15}, {OV7675_REG_MTX5, 0x7c}, {OV7675_REG_MTX6, 0x91}, {OV7675_REG_MTXS, 0x1e},
	{OV7675_REG_COM16, 0x38}, {OV7675_REG_REG76, 0xe0}, {OV7675_REG_AEW, 0x40}, {OV7675_REG_AEB, 0x38},
	{OV7675_REG_VPT, 0x91}, {OV7675_REG_SLOP, 0x09}, {OV7675_REG_GAM1, 0x0c}, {OV7675_REG_GAM2, 0x16},
	{OV7675_REG_GAM3, 0x28}, {OV7675_REG_GAM4, 0x48}, {OV7675_REG_GAM5, 0x57}, {OV7675_REG_GAM6, 0x64},
	{OV7675_REG_GAM7, 0x71}, {OV7675_REG_GAM8, 0x7e}, {OV7675_REG_GAM9, 0x89}, {OV7675_REG_GAM10, 0x94},
	{OV7675_REG_GAM11, 0xa8}, {OV7675_REG_GAM12, 0xba}, {OV7675_REG_GAM13, 0xd7}, {OV7675_REG_GAM14, 0xec},
	{OV7675_REG_GAM15, 0xf9}, {OV7675_REG_COM2, 0x00}, {OV7675_REG_SCALING_YSC, 0x35}, {OV7675_REG_COM1, 0x40},
};

static DEFINE_SEMAPHORE(regs_mutex);

/* The sensor's power-down line (GPG5): low runs it. The delays in ms: after
 * power-on, again before the bus wakes up, and after a resolution change. */
#define OV7675_PWDN_OFF		0
#define OV7675_POWERON_MS	20
#define OV7675_SETTLE_MS	100
#define OV7675_RESCHANGE_MS	200

/* The sensor's clock: camif-upll drives CAMCLKOUT at 6 MHz. */
#define OV7675_XCLK_HZ		6000000

static void __inline__ ov7675_poweron(void)
{
	gpio_direction_output(S3C2410_GPG(5), OV7675_PWDN_OFF);
	mdelay(OV7675_POWERON_MS);
}

static int __inline__ ov7675_check(void)
{
	u32 mid;

	mid = sccb_read(OV7675_SCCB_ADDR, OV7675_REG_MIDH)<<8;
	mid |= sccb_read(OV7675_SCCB_ADDR, OV7675_REG_MIDL);
	printk("OV7675 address 0x%02X, manufacture ID 0x%04X, expect 0x%04X\n", OV7675_SCCB_ADDR, mid, OV7675_MANUFACT_ID);

	return (mid==OV7675_MANUFACT_ID)?1:0;
}

static u32 __inline__ show_ov7675_product_id(void)
{
	u32 pid;

	pid = sccb_read(OV7675_SCCB_ADDR, OV7675_REG_PID)<<8;
	pid |= sccb_read(OV7675_SCCB_ADDR, OV7675_REG_VER);
	printk("OV7675 address 0x%02X, product ID 0x%04X, expect 0x%04X\n", OV7675_SCCB_ADDR, pid, OV7675_PRODUCT_ID);
	return pid;
}

static void ov7675_init_regs(void)
{
	int i;

	down(&regs_mutex);
	if (camera.resolution == CAMIF_RES_QVGA)
	{
		for (i=0; i<ARRAY_SIZE(regs_qvga); i++)
		{
			if (regs_qvga[i].subaddr == OV7675_REG_DELAY)
			{
				mdelay(regs_qvga[i].value);
				continue;
			}
			sccb_write(OV7675_SCCB_ADDR, regs_qvga[i].subaddr, regs_qvga[i].value);
		}
	}
	else if (camera.resolution == CAMIF_RES_VGA)
	{
		for (i=0; i<ARRAY_SIZE(regs_vga); i++)
		{
			if (regs_vga[i].subaddr == OV7675_REG_DELAY)
			{
				mdelay(regs_vga[i].value);
				continue;
			}
			sccb_write(OV7675_SCCB_ADDR, regs_vga[i].subaddr, regs_vga[i].value);
		}
	}
	up(&regs_mutex);
}

static int s3c2440_ov7675_init(void)
{
	printk(KERN_ALERT"Loading OV7675 driver 8. get_page at init. default:640*480......\n");
	/* power on. */
	ov7675_poweron();
	mdelay(OV7675_SETTLE_MS);

	/* wake up the SCCB bus. */
	CFG_WRITE(SIO_D);
	CFG_WRITE(SIO_C);
	High(SIO_C);
	Low(SIO_C);
	High(SIO_C);
	Low(SIO_C);
	High(SIO_C);

	/* check device. */
	if (ov7675_check() == 0 && ov7675_check() == 0)
	{
		printk(KERN_ERR"No OV7675 found!!!\n");
		return -ENODEV;
	}

	show_ov7675_product_id();
	ov7675_init_regs();

	if (init_image_buffer() < 0)
		printk(KERN_ERR"init_image_buffer error\n");
	else
		printk(KERN_ERR"init_image_buffer ok\n");

	printk("OV7675 init done!\n");
	return 0;
}

/*
 * camif_ioctl: switch the sensor between VGA and QVGA.
 */
static long camif_ioctl(struct file *file, unsigned int cmd, unsigned long arg)
{
	struct s3c2440camif_fh * fh;
	struct s3c2440camif_dev * pdev;
	int ret = 0;
	int resolution = CAMIF_RES_VGA;

	printk(KERN_ALERT"OV7675 camif_ioctl\n");

	fh = file->private_data;
	pdev = fh->dev;

	switch (cmd)
	{
	case CAMIF_IOCTL_SET_VGA:
		printk(KERN_ALERT"OV7675 camif_ioctl set VGA\n");
		resolution = CAMIF_RES_VGA;
		break;

	case CAMIF_IOCTL_SET_QVGA:
		printk(KERN_ALERT"OV7675 camif_ioctl set QVGA\n");
		resolution = CAMIF_RES_QVGA;
		break;

	default:
		ret = -1;	/* an unknown request returns -1, not an errno */
		break;
	}

	if (pdev->resolution != resolution)
	{
		printk(KERN_ALERT"OV7675 camif_ioctl set new resolution 2\n");
		disable_irq(IRQ_S3C2440_CAM_C);
		disable_irq(IRQ_S3C2440_CAM_P);
		hw_reset_camif();
		pdev->resolution = resolution;
		ov7675_init_regs();
		pdev->state = CAMIF_STATE_READY;
		init_camif_config(fh);
		soft_reset_camif();
		invalid_image_buffer();
		enable_irq(IRQ_S3C2440_CAM_P);
		enable_irq(IRQ_S3C2440_CAM_C);
		mdelay(OV7675_RESCHANGE_MS);
		ret = 1;	/* 1: the resolution changed */
	}

	printk(KERN_ALERT"OV7675 camif_ioctl done\n");
	return ret;
}

/*
 * the key methods/attributes for this video device.
 */
static struct file_operations camif_fops =
{
	.owner		= THIS_MODULE,
	.open		= camif_open,
	.release	= camif_release,
	.read		= camif_read,
	.unlocked_ioctl	= camif_ioctl,
};

static struct miscdevice misc = {
        .minor = MISC_DYNAMIC_MINOR,
        .name = CARD_NAME,
        .fops = &camif_fops,
};

/*
 * camif_init()
 */
static int __init camif_init(void)
{
	int ret;
	struct s3c2440camif_dev * pdev;
	struct clk * camif_upll_clk;

	printk(KERN_ALERT"initializing s3c2440 camera interface......\n");

	pdev = &camera;

	/* set gpio-j to camera mode. */
	/* GPJ0-12: the camera interface's own function (2 in GPJCON). */
	for (ret = 0; ret <= 12; ret++)
		s3c_gpio_cfgpin(S3C2410_GPJ(ret), S3C_GPIO_SFN(2));
	/* The SCCB lines, the sensor's power-down (GPG5) and GPG4, through
	 * gpiolib. */
	gpio_request(SIO_C, "ov7675-sio-c");
	gpio_request(SIO_D, "ov7675-sio-d");
	gpio_request(S3C2410_GPG(5), "ov7675-pwdn");
	gpio_request(S3C2410_GPG(4), "camera-gpg4");

	/* The buffers' device: a 32-bit DMA mask, which a misc device lacks. */
	camif_pdev = platform_device_register_full(&(struct platform_device_info){
		.name = "rabbitv3-camif", .id = -1, .dma_mask = DMA_BIT_MASK(32) });
	if (IS_ERR(camif_pdev)) {
		ret = PTR_ERR(camif_pdev);
		goto error1;
	}

	/* init camera's virtual memory. */
	if (!request_mem_region((unsigned long)S3C2440_PA_CAMIF, S3C2440_SZ_CAMIF, CARD_NAME))
	{
		ret = -EBUSY;
		goto error1;
	}

	/* remap the virtual memory. */
	camif_base_addr = (unsigned long)ioremap((unsigned long)S3C2440_PA_CAMIF, S3C2440_SZ_CAMIF);
	if (camif_base_addr == (unsigned long)NULL)
	{
		ret = -EBUSY;
		goto error2;
	}

	/* init camera clock. */
	pdev->clk = clk_get(NULL, "camif");
	if (IS_ERR(pdev->clk))
	{
		ret = -ENOENT;
		goto error3;
	}
	clk_prepare_enable(pdev->clk);

	/* The sensor's clock, CAMCLKOUT: UPLL (48 MHz) / ((3 + 1) x 2) = 6 MHz,
	 * as 2.6.32's clk_set_rate("camif-upll") left CAMDIVN. 6.1's clock
	 * tree has the mux and the divider but no rate to ask them for. */
	{
		u32 camdivn = __raw_readl(S3C2440_CAMDIVN);
		camdivn &= ~S3C2440_CAMDIVN_CAMCLK_MASK;
		camdivn |= S3C2440_CAMDIVN_CAMCLK_SEL | 3;
		__raw_writel(camdivn, S3C2440_CAMDIVN);
	}
	camif_upll_clk = clk_get(NULL, "camif-upll");
	if (!IS_ERR(camif_upll_clk))
		printk(KERN_INFO "rabbitv3-camif: camif-upll %lu Hz, CAMDIVN %08x\n",
		       clk_get_rate(camif_upll_clk), __raw_readl(S3C2440_CAMDIVN));
	mdelay(100);

	/* init reference counter and its mutex. */
	mutex_init(&pdev->rcmutex);
	pdev->rc = 0;

	/* init image input source. */
	pdev->input = 0;

	/* RABBITV3: VGA by default. */
	pdev->resolution = CAMIF_RES_VGA;

	/* init camif state and its lock. */
	pdev->state = CAMIF_STATE_FREE;

	/* init command code, command lock and the command wait queue. */
	pdev->cmdcode = CAMIF_CMD_NONE;
	init_waitqueue_head(&pdev->cmdqueue);

	/* register to videodev layer. */
	if (misc_register(&misc) < 0)
	{
		ret = -EBUSY;
		goto error4;
	}
	printk(KERN_ALERT"s3c2440 camif init done\n");

	sccb_init();
	hw_reset_camif();
	has_ov7675 = s3c2440_ov7675_init() >= 0;
	gpio_set_value(S3C2410_GPG(4), 1);	/* RABBITV3: GPG4 high; nothing here says what it drives */
	return 0;

error4:
	clk_put(pdev->clk);
error3:
	iounmap((void *)camif_base_addr);
error2:
	release_mem_region((unsigned long)S3C2440_PA_CAMIF, S3C2440_SZ_CAMIF);

error1:
	return ret;
}

/*
 * camif_cleanup()
 */
static void __exit camif_cleanup(void)
{
	struct s3c2440camif_dev *pdev;

	sccb_cleanup();

	pdev = &camera;

	misc_deregister(&misc);


	clk_put(pdev->clk);

	iounmap((void *)camif_base_addr);
	release_mem_region((unsigned long)S3C2440_PA_CAMIF, S3C2440_SZ_CAMIF);

	printk(KERN_ALERT"mini2440_camif: module removed\n");
}

MODULE_DESCRIPTION("v4l2 driver module for s3c2440 camera interface");
MODULE_AUTHOR("sunxq [baobaoba520@yahoo.com.cn]");
MODULE_LICENSE("GPL");

device_initcall(camif_init);
module_exit(camif_cleanup);

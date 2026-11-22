/* SPDX-License-Identifier: MIT */
/*
 * oobdump: a NAND partition as page + raw OOB, 512 + 16 bytes a page, read
 * through MEMREADOOB rather than busybox nanddump.
 *
 * Why (2026-11-22): the measure key's mtd6-oob.bin from Gengar, written by
 * busybox nanddump -o under its 2.6.32, had a blank OOB on the pages that
 * hold data (1,883 of the first 2,000 all FF), so the YAFFS1 tags were not
 * in it and the 6.1 kernel mounted an empty /usr. This asks the kernel for
 * every page's spare itself (MTD_OOB_PLACE: the raw bytes, where YAFFS1
 * wrote them) and the data with pread, bad blocks included.
 *
 *   oobdump /dev/mtd6 /mnt/usbkey/measure/mtd6-raw.bin
 *
 * Prints the pages read, how many had a spare that was not all FF, and
 * the first such spare, so a blank read shows on the key at once.
 */
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <mtd/mtd-user.h>

int main(int argc, char **argv)
{
	struct mtd_info_user info;
	unsigned char page[4096 + 256];
	unsigned long pages = 0, spares = 0, failed = 0, badblocks = 0;
	int err = 0;
	int in, first = 1;
	FILE *out;

	if (argc != 3) {
		fprintf(stderr, "usage: oobdump /dev/mtdN out.bin\n");
		return 2;
	}
	in = open(argv[1], O_RDONLY);
	if (in < 0 || ioctl(in, MEMGETINFO, &info) < 0) {
		perror(argv[1]);
		return 1;
	}
	if (info.writesize + info.oobsize > sizeof(page)) {
		fprintf(stderr, "page %u + oob %u too big\n", info.writesize, info.oobsize);
		return 1;
	}
	out = fopen(argv[2], "wb");
	if (!out) {
		perror(argv[2]);
		return 1;
	}
	for (uint32_t at = 0; at < info.size; at += info.writesize) {
		long long block = at - at % info.erasesize;	/* loff_t: 64-bit */
		struct mtd_oob_buf oob = { .start = at, .length = info.oobsize,
					   .ptr = page + info.writesize };
		int bad = ioctl(in, MEMGETBADBLOCK, &block) > 0;

		memset(page, 0xff, info.writesize + info.oobsize);
		/* Read even a block the kernel calls bad: its table may come from a
		 * marker byte that is not one (the busybox copy made 3,445 of
		 * Gengar's blocks "bad"); counted, not skipped. */
		if (bad && at % info.erasesize == 0)
			badblocks++;
		if (pread(in, page, info.writesize, at) != (ssize_t)info.writesize)
			memset(page, 0xff, info.writesize);
		if (ioctl(in, MEMREADOOB, &oob) < 0) {
			if (!failed++)
				err = errno;
			memset(page + info.writesize, 0xff, info.oobsize);
		}
		for (uint32_t i = 0; i < info.oobsize; i++)
			if (page[info.writesize + i] != 0xff) {
				spares++;
				if (first) {
					printf("first spare at page %lu:", pages);
					for (uint32_t j = 0; j < info.oobsize; j++)
						printf(" %02x", page[info.writesize + j]);
					printf("\n");
					first = 0;
				}
				break;
			}
		if (fwrite(page, 1, info.writesize + info.oobsize, out) != info.writesize + info.oobsize) {
			perror("write");
			return 1;
		}
		pages++;
	}
	fclose(out);
	printf("%s: %lu pages, %lu with a spare that is not all FF, %lu spare reads failed%s%s, %lu blocks marked bad\n",
	       argv[1], pages, spares, failed, failed ? ": " : "", failed ? strerror(err) : "", badblocks);
	return 0;
}

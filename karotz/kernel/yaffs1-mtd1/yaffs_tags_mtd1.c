// SPDX-License-Identifier: GPL-2.0-only
/*
 * YAFFS1 tags as the Karotz wrote them: Aleph One's yaffs_mtdif1.c of 2009
 * (Sergey Kubushyn and Charles Manning, commit 3e5718ec, the YAFFS in
 * LapinLibre's Linux 2.6.32), ported to today's tags handler (Terrier OS,
 * 2026-11-22).
 *
 * Today's YAFFS stores YAFFS1 tags the pre-2.6.17 way (yaffs_tagscompat.c:
 * a 16-byte spare written whole). Every kernel after 2.6.17 used this file
 * instead: the 8-byte packed tags (yaffs_packedtags1.c, unchanged since)
 * with their mini-ECC, written through MTD's free OOB bytes. On the
 * Karotz's NAND (512-byte pages, no ECC) those are bytes 8 to 15 of the
 * 16-byte OOB, in 2.6.32 as in 6.1, so a /usr written by the old kernel
 * reads here and the other way round; tagscompat's 16 bytes did not even
 * fit (every write failed its read-back on a simulated Karotz NAND).
 */

#include "yaffs_guts.h"
#include "yaffs_trace.h"
#include "yaffs_packedtags1.h"
#include "yaffs_tagscompat.h"
#include "yaffs_tags_mtd1.h"

#define YTAG1_SIZE 8	/* CONFIG_YAFFS_9BYTE_TAGS off, as on the Karotz */

/*
 * Write a chunk and its packed tags. A chunk being deleted is rewritten
 * once, its tags all ones but the (inverted) deleted bit: only zeroed bits
 * stick on NAND (the 2009 comment's reasoning, unchanged).
 */
static int yaffs_tags_mtd1_write(struct yaffs_dev *dev, int nand_chunk,
				 const u8 *data,
				 const struct yaffs_ext_tags *etags)
{
	struct yaffs_packed_tags1 pt1;

	BUILD_BUG_ON(sizeof(struct yaffs_packed_tags1) != 12);
	BUILD_BUG_ON(sizeof(struct yaffs_tags) != 8);

	yaffs_pack_tags1(&pt1, etags);
	yaffs_calc_tags_ecc((struct yaffs_tags *)&pt1);

	if (etags->is_deleted) {
		memset(&pt1, 0xff, 8);
		pt1.deleted = 0;
	}

	return dev->drv.drv_write_chunk_fn(dev, nand_chunk, data,
			data ? dev->param.total_bytes_per_chunk : 0,
			(const u8 *)&pt1, YTAG1_SIZE);
}

static int yaffs_tags_mtd1_unread(struct yaffs_dev *dev, int nand_chunk,
				  struct yaffs_ext_tags *etags, int retval)
{
	if (etags) {
		memset(etags, 0, sizeof(*etags));
		etags->ecc_result = YAFFS_ECC_RESULT_UNFIXED;
		etags->block_bad = dev->drv.drv_check_bad_fn &&
			dev->drv.drv_check_bad_fn(dev,
				nand_chunk / dev->param.chunks_per_block) != YAFFS_OK;
	}
	return retval;
}

/*
 * Read a chunk and its tags: blank tags give empty tags; otherwise the
 * deleted bit is read and set back before the mini-ECC check, which may
 * correct one bit.
 */
static int yaffs_tags_mtd1_read(struct yaffs_dev *dev, int nand_chunk,
				u8 *data, struct yaffs_ext_tags *etags)
{
	struct yaffs_packed_tags1 pt1;
	enum yaffs_ecc_result eccres = YAFFS_ECC_RESULT_NO_ERROR;
	int deleted;
	int r;

	memset(&pt1, 0xff, sizeof(pt1));
	r = dev->drv.drv_read_chunk_fn(dev, nand_chunk, data,
			data ? dev->param.total_bytes_per_chunk : 0,
			(u8 *)&pt1, YTAG1_SIZE, &eccres);
	if (r != YAFFS_OK || eccres == YAFFS_ECC_RESULT_UNFIXED)
		return yaffs_tags_mtd1_unread(dev, nand_chunk, etags, YAFFS_FAIL);

	if (!etags)
		return YAFFS_OK;

	if (yaffs_check_ff((u8 *)&pt1, 8)) {
		memset(etags, 0, sizeof(*etags));
		etags->ecc_result = YAFFS_ECC_RESULT_NO_ERROR;
		return YAFFS_OK;
	}

	deleted = !pt1.deleted;
	pt1.deleted = 1;

	switch (yaffs_check_tags_ecc((struct yaffs_tags *)&pt1)) {
	case 0:
		break;
	case 1:
		dev->n_tags_ecc_fixed++;
		if (eccres == YAFFS_ECC_RESULT_NO_ERROR)
			eccres = YAFFS_ECC_RESULT_FIXED;
		break;
	default:
		dev->n_tags_ecc_unfixed++;
		memset(etags, 0, sizeof(*etags));
		etags->ecc_result = YAFFS_ECC_RESULT_UNFIXED;
		return YAFFS_FAIL;
	}

	pt1.should_be_ff = 0xffffffff;
	yaffs_unpack_tags1(etags, &pt1);
	etags->ecc_result = eccres;
	etags->is_deleted = deleted;
	return YAFFS_OK;
}

static int yaffs_tags_mtd1_mark_bad(struct yaffs_dev *dev, int block_no)
{
	yaffs_trace(YAFFS_TRACE_BAD_BLOCKS, "marking block %d bad", block_no);
	return dev->drv.drv_mark_bad_fn(dev, block_no);
}

/* The state of a block from the tags of its first chunk. Always succeeds. */
static int yaffs_tags_mtd1_query_block(struct yaffs_dev *dev, int block_no,
				       enum yaffs_block_state *state,
				       u32 *seq_number)
{
	struct yaffs_ext_tags etags;
	int chunk = block_no * dev->param.chunks_per_block;

	*seq_number = 0;
	yaffs_tags_mtd1_read(dev, chunk, NULL, &etags);

	if (dev->drv.drv_check_bad_fn &&
	    dev->drv.drv_check_bad_fn(dev, block_no) != YAFFS_OK) {
		yaffs_trace(YAFFS_TRACE_BAD_BLOCKS, "block %d is marked bad",
			block_no);
		*state = YAFFS_BLOCK_STATE_DEAD;
	} else if (etags.ecc_result != YAFFS_ECC_RESULT_NO_ERROR) {
		*state = YAFFS_BLOCK_STATE_NEEDS_SCAN;
	} else if (etags.chunk_used) {
		*state = YAFFS_BLOCK_STATE_NEEDS_SCAN;
		*seq_number = etags.seq_number;
	} else {
		*state = YAFFS_BLOCK_STATE_EMPTY;
	}
	return YAFFS_OK;
}

void yaffs_tags_mtd1_install(struct yaffs_dev *dev)
{
	if (dev->param.is_yaffs2)
		return;
	dev->tagger.write_chunk_tags_fn = yaffs_tags_mtd1_write;
	dev->tagger.read_chunk_tags_fn = yaffs_tags_mtd1_read;
	dev->tagger.query_block_fn = yaffs_tags_mtd1_query_block;
	dev->tagger.mark_bad_fn = yaffs_tags_mtd1_mark_bad;
}

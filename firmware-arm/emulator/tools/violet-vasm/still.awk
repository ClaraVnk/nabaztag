# The Terrier's wait program without Antoine Schmitt's clip (TaiChiPop,
# 1FEV06): everything between @data and its closing `FCB 0,end` goes, the
# label and the end stay.
#
#   LC_ALL=C awk -f still.awk Nabaztag_wait_terrier.vasm > Nabaztag_wait_terrier_still.vasm
#
# Why (2026-11-24): the clip is 26 KB of a 33.8 KB frame and plays only when
# the owner turned on "moves by itself" (source 14, `@getrespire`, off by
# default on the Terrier); every v1 downloads the frame at each boot. The
# 25 symbols the clip defines (/m1-/m24, /mus24) are used nowhere else.
# Line ends are kept as they are (the vendored file is CRLF, latin-1:
# LC_ALL=C).
BEGIN { skip = 0 }
skip && /^[ \t]*FCB[ \t]+0,end/ { skip = 0 }
!skip { print }
/^@data[ \t\r]*$/ { skip = 1; n++ }
END { if (n != 1) { print "still.awk: @data found " n " times" > "/dev/stderr"; exit 1 } }

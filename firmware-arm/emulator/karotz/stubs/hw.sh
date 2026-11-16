# sourced by the recorders: one line per hardware request, to /proof/hw.log
hw() { printf '%s %s\n' "$(date +%s.%N | cut -c1-14)" "$*" >> /proof/hw.log; }

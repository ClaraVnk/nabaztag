FEATURE = 1

if FEATURE:
    def flavour():
        return 1
else:
    def flavour():
        return 0

if not MISSING:
    def extra():
        return 2


def main():
    return flavour() + extra()

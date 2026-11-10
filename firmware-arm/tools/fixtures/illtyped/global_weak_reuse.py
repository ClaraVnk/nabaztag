# A global initialised to None is a weak variable: its first use fixes it
# for the whole program, so this list of I then list of S is refused.
acc = None


def main():
    acc = lst(1)
    acc = lst("a")

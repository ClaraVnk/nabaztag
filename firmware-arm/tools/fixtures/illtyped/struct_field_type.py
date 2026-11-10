# Point's x is declared int and set to a string.
class Point:
    x: int
    y: int


def main():
    p = Point(x="one", y=2)
    return p.y

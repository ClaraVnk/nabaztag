# An unannotated field settles to I at its first use, then gets an S.
class Point:
    x: object
    y: object


def main():
    p = Point(x=1, y=2)
    p.x = "one"
    return p.y

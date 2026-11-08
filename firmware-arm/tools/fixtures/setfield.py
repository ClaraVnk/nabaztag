class Point:
    x: int
    y: int
    z: int


def main():
    p = Point(x=1, y=2, z=3)
    p.x = 99
    return p.x

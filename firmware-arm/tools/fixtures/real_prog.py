def fact(n):
    if n <= 1:
        return 1
    else:
        return n * fact(n - 1)


def classify(n):
    if n < 0:
        return 0
    elif n == 0:
        return 1
    elif n < 10:
        return 2
    else:
        return 3


def main():
    a = 5
    b = a + 1
    c = b * 2
    d = fact(a)
    return classify(a + b + c + d)

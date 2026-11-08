class State:
    idle = ()
    connecting = (int,)
    connected = (int,)
    failed = (int,)


tries = 0
rank = 0


def score(s):
    match s:
        case idle:
            return 0
        case connecting(n):
            if n < 3:
                return n
            else:
                return 3
        case connected(n):
            return 4
        case failed(n):
            return 5


def step(s):
    match s:
        case idle:
            return connecting(0)
        case connecting(n):
            if n < 3:
                return connecting(n + 1)
            else:
                return failed(n)
        case connected(n):
            return connected(n)
        case failed(n):
            match connecting(n):
                case connecting(m):
                    return idle
                case _:
                    return idle


def main():
    tries = tries + 1
    rank = score(step(step(idle)))
    return rank

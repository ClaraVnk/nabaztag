class Msg:
    Ping = ()
    Pong = (int,)


def handle(x):
    match x:
        case Ping:
            return 0
        case Pong(p):
            return p


def main():
    return handle(Pong(7))

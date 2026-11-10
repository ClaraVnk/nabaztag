# The first draft of statemachine.py: a State stored in a global that was
# initialised to 0.
class State:
    idle = ()
    connecting = (object,)
    connected = (object,)
    failed = (object,)


current = 0


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
            return idle


def main():
    current = step(idle)

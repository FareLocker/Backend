# hacklanta

A [Spectrum](https://photon.codes/docs/spectrum-ts) project. Wired with: imessage.

## Environment

Before running, open `.env` and fill in the values:

From your project Settings on the [Photon dashboard](https://app.photon.codes):

- `PROJECT_ID`
- `PROJECT_SECRET`

Set `FARELOCK_API_URL` to the FareLocker API base URL. It defaults to

## Run

```sh
bun install
bun start
```

## FareLock messages

Text FareLock naturally, for example:

```text
I booked from Atlanta to LAX next Friday for $284. How much is the FareLock?
```

The service forwards the message to FareLocker’s quote API and replies with
the fee and maximum price-drop protection. The FareLocker API must be running
and have pricing data for the requested route.


## Where to go next

- [Spectrum docs](https://photon.codes/docs/spectrum-ts)
- Edit `src/index.ts` to replace the echo loop with real agent logic.
- Add more providers from `spectrum-ts/providers/*`.

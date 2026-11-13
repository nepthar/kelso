FROM golang:1.27.3-alpine@sha256:fa3d596898d9f126af7c01d14ee7597bab3f39cccf392ef36b69fe77cd681f18 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ui.html ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /kproxy .

FROM scratch
COPY --from=build /kproxy /kproxy
ENTRYPOINT ["/kproxy"]

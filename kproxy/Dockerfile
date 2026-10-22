FROM golang:1.27.3-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ui.html ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /kproxy .

FROM scratch
COPY --from=build /kproxy /kproxy
ENTRYPOINT ["/kproxy"]

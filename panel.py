import uvicorn


if __name__ == "__main__":
    uvicorn.run("deathtg.panel.server:app", host="127.0.0.1", port=8080, reload=False)

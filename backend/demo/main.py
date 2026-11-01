import asyncio
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

app = FastAPI(title="EdgeTest Demo API")
users: Dict[int, "User"] = {}
next_user_id = 3
users_lock = asyncio.Lock()


class UserInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254)

    model_config = ConfigDict(extra="forbid")


class User(UserInput):
    id: int


users[1] = User(id=1, name="Ada Lovelace", email="ada@example.test")
users[2] = User(id=2, name="Grace Hopper", email="grace@example.test")


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok", "service": "EdgeTest Demo API"}


@app.get("/users", response_model=List[User])
async def list_users() -> List[User]:
    async with users_lock:
        return list(users.values())


@app.get("/users/{user_id}", response_model=User)
async def get_user(user_id: int) -> User:
    async with users_lock:
        user = users.get(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        return user


@app.post("/users", response_model=User, status_code=status.HTTP_201_CREATED)
async def create_user(user_input: UserInput) -> User:
    global next_user_id
    async with users_lock:
        user = User(id=next_user_id, **user_input.model_dump())
        users[next_user_id] = user
        next_user_id += 1
        return user


@app.put("/users/{user_id}", response_model=User)
async def replace_user(user_id: int, user_input: UserInput) -> User:
    async with users_lock:
        if user_id not in users:
            raise HTTPException(status_code=404, detail="User not found")
        user = User(id=user_id, **user_input.model_dump())
        users[user_id] = user
        return user


@app.patch("/users/{user_id}", response_model=User)
async def update_user(
    user_id: int,
    user_input: Dict[str, object],
) -> User:
    allowed_fields = {"name", "email"}
    if not user_input or not set(user_input).issubset(allowed_fields):
        raise HTTPException(status_code=422, detail="Invalid user fields")

    async with users_lock:
        current_user = users.get(user_id)
        if current_user is None:
            raise HTTPException(status_code=404, detail="User not found")
        updated_fields = current_user.model_dump()
        updated_fields.update(user_input)
        user = User.model_validate(updated_fields)
        users[user_id] = user
        return user


@app.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int) -> Response:
    async with users_lock:
        if users.pop(user_id, None) is None:
            raise HTTPException(status_code=404, detail="User not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/delay")
async def delay(seconds: float = Query(default=2.0, ge=0, le=10)) -> Dict[str, float]:
    await asyncio.sleep(seconds)
    return {"delayed_seconds": seconds}


@app.get("/errors/404")
async def not_found() -> None:
    raise HTTPException(status_code=404, detail="Demo not found")


@app.get("/errors/500")
async def server_error() -> None:
    raise HTTPException(status_code=500, detail="Demo server error")

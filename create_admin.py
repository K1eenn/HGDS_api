from authentication import create_user

success, message = create_user(
    username="admin",
    password="hoangtrungKien1",
    full_name="Admin User",
    email="admin@example.com",
    is_admin=True
)

print(message)
-- 应用运行角色：非超级用户，用于让 RLS 真正生效。
-- 迁移与引导脚本使用超级用户 agent 连接。
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_app') THEN
        CREATE ROLE agent_app LOGIN PASSWORD 'agent_app' NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END
$$;

GRANT CONNECT ON DATABASE agent_platform TO agent_app;

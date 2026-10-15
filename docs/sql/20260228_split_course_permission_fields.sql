-- 实验课程系统迁移脚本（2026-02-28）
-- 目标：
-- 1) 课程创建人使用独立列：oj_course.creator_username
-- 2) 课程管理组使用独立列：oj_course.manager_groups（JSON数组字符串）
-- 3) 课程绑定组继续使用既有独立列：oj_course.group_id
--
-- 说明：
-- - 该脚本面向 MySQL 5.7+/8.0+。
-- - 建议先在测试环境执行并备份数据库。

START TRANSACTION;

-- 1. 新增字段（仅在不存在时添加）
SET @db_name = DATABASE();

SET @sql = (
    SELECT IF(
        EXISTS(
            SELECT 1
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = @db_name
              AND TABLE_NAME = 'oj_course'
              AND COLUMN_NAME = 'creator_username'
        ),
        'SELECT ''creator_username already exists''',
        'ALTER TABLE oj_course ADD COLUMN creator_username VARCHAR(100) NULL COMMENT ''课程创建人用户名'' AFTER create_time'
    )
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @sql = (
    SELECT IF(
        EXISTS(
            SELECT 1
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = @db_name
              AND TABLE_NAME = 'oj_course'
              AND COLUMN_NAME = 'manager_groups'
        ),
        'SELECT ''manager_groups already exists''',
        'ALTER TABLE oj_course ADD COLUMN manager_groups LONGTEXT NULL COMMENT ''课程管理组ID列表(JSON)'' AFTER creator_username'
    )
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 2. 索引（仅在不存在时添加）
SET @sql = (
    SELECT IF(
        EXISTS(
            SELECT 1
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = @db_name
              AND TABLE_NAME = 'oj_course'
              AND INDEX_NAME = 'idx_oj_course_creator_username'
        ),
        'SELECT ''idx_oj_course_creator_username already exists''',
        'CREATE INDEX idx_oj_course_creator_username ON oj_course (creator_username)'
    )
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 3. 从 ext_config 回填新字段
-- 3.1 回填 creator_username
UPDATE oj_course
SET creator_username = NULLIF(TRIM(JSON_UNQUOTE(JSON_EXTRACT(ext_config, '$.creator_username'))), '')
WHERE (creator_username IS NULL OR creator_username = '')
  AND JSON_VALID(ext_config) = 1
  AND JSON_TYPE(JSON_EXTRACT(ext_config, '$.creator_username')) = 'STRING';

-- 3.2 回填 manager_groups（仅接受数组）
UPDATE oj_course
SET manager_groups = JSON_EXTRACT(ext_config, '$.manager_groups')
WHERE (manager_groups IS NULL OR manager_groups = '' OR manager_groups = 'null')
  AND JSON_VALID(ext_config) = 1
  AND JSON_TYPE(JSON_EXTRACT(ext_config, '$.manager_groups')) = 'ARRAY';

-- 4. 清理 ext_config 中已迁移的旧键（可选，建议执行）
UPDATE oj_course
SET ext_config = JSON_REMOVE(ext_config, '$.creator_username', '$.manager_groups')
WHERE JSON_VALID(ext_config) = 1
  AND (
      JSON_CONTAINS_PATH(ext_config, 'one', '$.creator_username')
      OR JSON_CONTAINS_PATH(ext_config, 'one', '$.manager_groups')
  );

COMMIT;

-- ------------------------------------------------------------
-- 可选回滚脚本（按需手动执行，默认不要执行）
-- ------------------------------------------------------------
-- START TRANSACTION;
-- UPDATE oj_course
-- SET ext_config = CASE
--     WHEN JSON_VALID(ext_config) = 1 THEN
--         JSON_SET(
--             JSON_SET(ext_config, '$.creator_username', COALESCE(creator_username, CAST(NULL AS JSON))),
--             '$.manager_groups',
--             CASE
--                 WHEN manager_groups IS NULL OR manager_groups = '' OR JSON_VALID(manager_groups) = 0 THEN CAST(NULL AS JSON)
--                 ELSE CAST(manager_groups AS JSON)
--             END
--         )
--     ELSE ext_config
-- END;
-- DROP INDEX idx_oj_course_creator_username ON oj_course;
-- ALTER TABLE oj_course DROP COLUMN manager_groups;
-- ALTER TABLE oj_course DROP COLUMN creator_username;
-- COMMIT;

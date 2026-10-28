-- Review-only MySQL 8 schema. Use python -m auto_task.migrate --apply for idempotent application.

CREATE TABLE IF NOT EXISTS auto_task_job (
	id VARCHAR(63) NOT NULL, 
	owner_id VARCHAR(64) NOT NULL, 
	owner_username VARCHAR(100) NOT NULL, 
	payer_id VARCHAR(64) NOT NULL, 
	operation_key VARCHAR(128) COLLATE utf8mb4_bin NOT NULL, 
	task_type VARCHAR(63) NOT NULL, 
	payload LONGTEXT NOT NULL, 
	input_hash VARCHAR(64) NOT NULL, 
	resource_id VARCHAR(63), 
	course_id INTEGER, 
	psid INTEGER, 
	status VARCHAR(24) NOT NULL, 
	attempt INTEGER NOT NULL, 
	max_attempts INTEGER NOT NULL, 
	lease_token VARCHAR(64), 
	lease_until DATETIME(6), 
	available_at DATETIME(6) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	finished_at DATETIME(6), 
	result LONGTEXT, 
	error TEXT, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_ai_job_operation UNIQUE (owner_id, operation_key), 
	FOREIGN KEY(id) REFERENCES auto_task_run (id)
);

CREATE INDEX ix_ai_job_claim ON auto_task_job (status, available_at, lease_until);

CREATE INDEX ix_auto_task_job_course_id ON auto_task_job (course_id);

CREATE INDEX ix_auto_task_job_owner_id ON auto_task_job (owner_id);

CREATE INDEX ix_auto_task_job_psid ON auto_task_job (psid);

CREATE INDEX ix_auto_task_job_resource_id ON auto_task_job (resource_id);

CREATE TABLE IF NOT EXISTS auto_task_account (
	subject_id VARCHAR(64) NOT NULL, 
	username VARCHAR(100) NOT NULL, 
	balance NUMERIC(20, 6) NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	permanent INTEGER NOT NULL, 
	reason TEXT, 
	updated_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (subject_id)
);

CREATE TABLE IF NOT EXISTS auto_task_resource (
	id VARCHAR(63) NOT NULL, 
	kind VARCHAR(40) NOT NULL, 
	owner_id VARCHAR(64) NOT NULL, 
	owner_username VARCHAR(100) NOT NULL, 
	course_id INTEGER, 
	psid INTEGER, 
	parent_id VARCHAR(63), 
	version INTEGER NOT NULL, 
	state VARCHAR(24) NOT NULL, 
	data LONGTEXT NOT NULL, 
	search_text LONGTEXT NOT NULL, 
	canonical_key VARCHAR(64), 
	deleted INTEGER NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	updated_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (canonical_key)
);

CREATE FULLTEXT INDEX ft_ai_resource_search ON auto_task_resource (search_text) WITH PARSER ngram;

CREATE INDEX ix_auto_task_resource_course_id ON auto_task_resource (course_id);

CREATE INDEX ix_auto_task_resource_kind ON auto_task_resource (kind);

CREATE INDEX ix_auto_task_resource_owner_id ON auto_task_resource (owner_id);

CREATE INDEX ix_auto_task_resource_parent_id ON auto_task_resource (parent_id);

CREATE INDEX ix_auto_task_resource_psid ON auto_task_resource (psid);

CREATE TABLE IF NOT EXISTS auto_task_review (
	id VARCHAR(63) NOT NULL, 
	subject_id VARCHAR(64) NOT NULL, 
	task_id VARCHAR(63), 
	decision VARCHAR(24) NOT NULL, 
	reason TEXT NOT NULL, 
	reviewer_id VARCHAR(64), 
	created_at DATETIME(6) NOT NULL, 
	reviewed_at DATETIME(6), 
	PRIMARY KEY (id)
);

CREATE INDEX ix_auto_task_review_subject_id ON auto_task_review (subject_id);

CREATE INDEX ix_auto_task_review_task_id ON auto_task_review (task_id);

CREATE TABLE IF NOT EXISTS auto_task_setting (
	id VARCHAR(40) NOT NULL, 
	data TEXT NOT NULL, 
	secret TEXT, 
	version INTEGER NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS auto_task_ai_run (
	id VARCHAR(63) NOT NULL, 
	task_id VARCHAR(63) NOT NULL, 
	attempt INTEGER NOT NULL, 
	model VARCHAR(128) NOT NULL, 
	`usage` TEXT NOT NULL, 
	cost_usd NUMERIC(20, 9) NOT NULL, 
	session_id VARCHAR(128), 
	status VARCHAR(24) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_auto_task_ai_run_task_id ON auto_task_ai_run (task_id);

CREATE TABLE IF NOT EXISTS auto_task_file (
	id VARCHAR(63) NOT NULL, 
	owner_id VARCHAR(64) NOT NULL, 
	resource_id VARCHAR(63), 
	filename VARCHAR(255) NOT NULL, 
	media_type VARCHAR(128) NOT NULL, 
	path VARCHAR(255) NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	size INTEGER NOT NULL, 
	sequence_key VARCHAR(128), 
	created_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (path), 
	UNIQUE (sequence_key)
);

CREATE INDEX ix_auto_task_file_owner_id ON auto_task_file (owner_id);

CREATE INDEX ix_auto_task_file_resource_id ON auto_task_file (resource_id);

CREATE TABLE IF NOT EXISTS auto_task_ledger (
	id VARCHAR(63) NOT NULL, 
	subject_id VARCHAR(64) NOT NULL, 
	billing_key VARCHAR(128) COLLATE utf8mb4_bin NOT NULL, 
	task_id VARCHAR(63), 
	amount NUMERIC(20, 6) NOT NULL, 
	cost_usd NUMERIC(20, 9) NOT NULL, 
	actor_id VARCHAR(64) NOT NULL, 
	reason TEXT NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(subject_id) REFERENCES auto_task_account (subject_id), 
	UNIQUE (billing_key)
);

CREATE INDEX ix_auto_task_ledger_subject_id ON auto_task_ledger (subject_id);

CREATE INDEX ix_auto_task_ledger_task_id ON auto_task_ledger (task_id);

CREATE TABLE IF NOT EXISTS auto_task_file_link (
	file_id VARCHAR(63) NOT NULL, 
	resource_id VARCHAR(63) NOT NULL, 
	PRIMARY KEY (file_id, resource_id), 
	FOREIGN KEY(file_id) REFERENCES auto_task_file (id), 
	FOREIGN KEY(resource_id) REFERENCES auto_task_resource (id)
);

CREATE TABLE IF NOT EXISTS auto_task_input (
	task_id VARCHAR(63) NOT NULL, 
	resource_version INTEGER NOT NULL, 
	resource_kind VARCHAR(40) NOT NULL, 
	content LONGTEXT NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (task_id), 
	FOREIGN KEY(task_id) REFERENCES auto_task_job (id)
);

CREATE TABLE IF NOT EXISTS auto_task_revision (
	id VARCHAR(63) NOT NULL, 
	resource_id VARCHAR(63) NOT NULL, 
	version INTEGER NOT NULL, 
	state VARCHAR(24) NOT NULL, 
	deleted INTEGER NOT NULL, 
	data LONGTEXT NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	actor_id VARCHAR(64) NOT NULL, 
	task_id VARCHAR(63), 
	created_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(resource_id) REFERENCES auto_task_resource (id)
);

CREATE INDEX ix_auto_task_revision_resource_id ON auto_task_revision (resource_id);

CREATE TABLE IF NOT EXISTS auto_task_plagiarism_snapshot (
	id VARCHAR(63) NOT NULL, 
	resource_id VARCHAR(63) NOT NULL, 
	resource_version INTEGER NOT NULL, 
	psid INTEGER NOT NULL, 
	group_id INTEGER NOT NULL, 
	pid INTEGER NOT NULL, 
	selection_policy VARCHAR(24) NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	count INTEGER NOT NULL, 
	created_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(resource_id) REFERENCES auto_task_resource (id)
);

CREATE INDEX ix_auto_task_plagiarism_snapshot_group_id ON auto_task_plagiarism_snapshot (group_id);

CREATE INDEX ix_auto_task_plagiarism_snapshot_pid ON auto_task_plagiarism_snapshot (pid);

CREATE INDEX ix_auto_task_plagiarism_snapshot_psid ON auto_task_plagiarism_snapshot (psid);

CREATE INDEX ix_auto_task_plagiarism_snapshot_resource_id ON auto_task_plagiarism_snapshot (resource_id);

CREATE TABLE IF NOT EXISTS auto_task_plagiarism_submission (
	id VARCHAR(63) NOT NULL, 
	snapshot_id VARCHAR(63) NOT NULL, 
	answer_detail_id INTEGER NOT NULL, 
	native_submission_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL, 
	user_id VARCHAR(64) NOT NULL, 
	username VARCHAR(100) NOT NULL, 
	code LONGTEXT NOT NULL, 
	code_sha256 VARCHAR(64) NOT NULL, 
	gmt_create DATETIME(6), 
	judge_result INTEGER, 
	judge_score INTEGER, 
	created_at DATETIME(6) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_ai_plag_snapshot_submission UNIQUE (snapshot_id, native_submission_id), 
	FOREIGN KEY(snapshot_id) REFERENCES auto_task_plagiarism_snapshot (id)
);

CREATE INDEX ix_auto_task_plagiarism_submission_answer_detail_id ON auto_task_plagiarism_submission (answer_detail_id);

CREATE INDEX ix_auto_task_plagiarism_submission_snapshot_id ON auto_task_plagiarism_submission (snapshot_id);

CREATE INDEX ix_auto_task_plagiarism_submission_user_id ON auto_task_plagiarism_submission (user_id);

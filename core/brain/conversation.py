class ContextState:
    def __init__(self):
        self.current_task = None
        self.current_goal = None
        self.current_app = None
        self.current_object = None
        self.recent_entities = []
        self.recent_actions = []
        self.task_history = []
        self.conversation_context = {}

    def update_from_interaction(self, **kwargs):
        for key, value in kwargs.items():
            if value is not None:
                setattr(self, key, value)

    def snapshot(self):
        return {
            "current_task": self.current_task,
            "current_goal": self.current_goal,
            "current_app": self.current_app,
            "current_object": self.current_object,
            "recent_entities": list(self.recent_entities),
            "recent_actions": list(self.recent_actions),
            "task_history": list(self.task_history),
            "conversation_context": dict(self.conversation_context),
        }


class Conversation:
    def __init__(self, max_history=10):
        self.max_history = max_history
        self.messages = []
        self.context = ContextState()

    def add_user_message(self, content):
        self.messages.append({
            "role": "user",
            "content": content
        })

        self._limit_history()

    def add_assistant_message(self, content):
        self.messages.append({
            "role": "assistant",
            "content": content
        })

        self._limit_history()

    def get_messages(self):
        return self.messages.copy()

    def clear(self):
        self.messages.clear()

    def _limit_history(self):
        max_messages = self.max_history * 2

        if len(self.messages) > max_messages:
            self.messages = self.messages[-max_messages:]

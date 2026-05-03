from src.agent.graph import agent_graph, build_graph


class TestGraphStructure:
    def test_nodes_present(self):
        graph = agent_graph.get_graph()
        nodes = list(graph.nodes.keys())
        assert "init" in nodes
        assert "load_memories_and_knowledge" in nodes
        assert "plan" in nodes
        assert "orchestrate" in nodes
        assert "exec_task" in nodes
        assert "synthesize" in nodes
        assert "final_answer" in nodes

    def test_entry_point(self):
        graph = agent_graph.get_graph()
        edges = list(graph.edges)
        start_edges = [e for e in edges if e.source == "__start__"]
        assert len(start_edges) == 1
        assert start_edges[0].target == "init"

    def test_conditional_from_orchestrate(self):
        graph = agent_graph.get_graph()
        edges = list(graph.edges)
        cond = [e for e in edges if e.source == "orchestrate" and e.conditional]
        assert len(cond) == 3  # dispatch_sub_agent, exec_task, synthesize

    def test_final_to_end(self):
        graph = agent_graph.get_graph()
        edges = list(graph.edges)
        end_edges = [e for e in edges if e.target == "__end__"]
        assert len(end_edges) == 1
        assert end_edges[0].source == "final_answer"

    def test_build_is_idempotent(self):
        g1 = build_graph()
        g2 = build_graph()
        assert g1 is not g2  # new instance each time

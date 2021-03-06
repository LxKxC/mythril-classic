from z3 import *
from mythril.analysis.ops import *
from mythril.analysis.report import Issue
from mythril.analysis import solver
from mythril.analysis.swc_data import REENTRANCY
from mythril.analysis.modules.base import DetectionModule
import re
import logging
from mythril.laser.ethereum.cfg import JumpType


class ExternalCallModule(DetectionModule):
    def __init__(self, max_search_depth=64):
        super().__init__(
            name="External Calls",
            swc_id=REENTRANCY,
            hooks=["CALL"],
            description="Check for call.value()() to external addresses",
        )
        self.max_search_depth = max_search_depth
        self.calls_visited = []

    def search_children(
        self, statespace, node, transaction_id, start_index=0, depth=0, results=None
    ):
        if results is None:
            results = []
        logging.debug("SEARCHING NODE %d", node.uid)

        if depth < self.max_search_depth:

            n_states = len(node.states)

            if n_states > start_index:

                for j in range(start_index, n_states):
                    if (
                        node.states[j].get_current_instruction()["opcode"] == "SSTORE"
                        and node.states[j].current_transaction.id == transaction_id
                    ):
                        results.append(
                            node.states[j].get_current_instruction()["address"]
                        )
            children = []

            for edge in statespace.edges:
                if edge.node_from == node.uid and edge.type != JumpType.Transaction:
                    children.append(statespace.nodes[edge.node_to])

            if len(children):
                for node in children:
                    results += self.search_children(
                        statespace,
                        node,
                        transaction_id,
                        depth=depth + 1,
                        results=results,
                    )

        return results

    def execute(self, statespace):

        issues = []

        for call in statespace.calls:

            state = call.state
            address = state.get_current_instruction()["address"]

            if call.type == "CALL":

                logging.debug(
                    "[EXTERNAL_CALLS] Call to: %s, value = %s, gas = %s"
                    % (str(call.to), str(call.value), str(call.gas))
                )

                if (
                    call.to.type == VarType.SYMBOLIC
                    and (call.gas.type == VarType.CONCRETE and call.gas.val > 2300)
                    or (
                        call.gas.type == VarType.SYMBOLIC
                        and "2300" not in str(call.gas)
                    )
                ):

                    description = "This contract executes a message call to "

                    target = str(call.to)
                    user_supplied = False

                    if "calldata" in target or "caller" in target:

                        if "calldata" in target:
                            description += (
                                "an address provided as a function argument. "
                            )
                        else:
                            description += "the address of the transaction sender. "

                        user_supplied = True
                    else:
                        m = re.search(r"storage_([a-z0-9_&^]+)", str(call.to))

                        if m:
                            idx = m.group(1)

                            func = statespace.find_storage_write(
                                state.environment.active_account.address, idx
                            )

                            if func:

                                description += (
                                    "an address found at storage slot "
                                    + str(idx)
                                    + ". "
                                    + "This storage slot can be written to by calling the function `"
                                    + func
                                    + "`. "
                                )
                                user_supplied = True

                    if user_supplied:

                        description += (
                            "Generally, it is not recommended to call user-supplied addresses using Solidity's call() construct. "
                            "Note that attackers might leverage reentrancy attacks to exploit race conditions or manipulate this contract's state."
                        )

                        issue = Issue(
                            contract=call.node.contract_name,
                            function_name=call.node.function_name,
                            address=address,
                            title="Message call to external contract",
                            _type="Warning",
                            description=description,
                            bytecode=state.environment.code.bytecode,
                            swc_id=REENTRANCY,
                            gas_used=(
                                state.mstate.min_gas_used,
                                state.mstate.max_gas_used,
                            ),
                        )

                    else:

                        description += "to another contract. Make sure that the called contract is trusted and does not execute user-supplied code."

                        issue = Issue(
                            contract=call.node.contract_name,
                            function_name=call.node.function_name,
                            address=address,
                            title="Message call to external contract",
                            _type="Informational",
                            description=description,
                            bytecode=state.environment.code.bytecode,
                            swc_id=REENTRANCY,
                            gas_used=(
                                state.mstate.min_gas_used,
                                state.mstate.max_gas_used,
                            ),
                        )

                    issues.append(issue)

                    if address not in self.calls_visited:
                        self.calls_visited.append(address)

                        logging.debug(
                            "[EXTERNAL_CALLS] Checking for state changes starting from "
                            + call.node.function_name
                        )

                        # Check for SSTORE in remaining instructions in current node & nodes down the CFG

                        state_change_addresses = self.search_children(
                            statespace,
                            call.node,
                            call.state.current_transaction.id,
                            call.state_index + 1,
                            depth=0,
                            results=[],
                        )

                        logging.debug(
                            "[EXTERNAL_CALLS] Detected state changes at addresses: "
                            + str(state_change_addresses)
                        )

                        if len(state_change_addresses):
                            for address in state_change_addresses:
                                description = (
                                    "The contract account state is changed after an external call. "
                                    "Consider that the called contract could re-enter the function before this "
                                    "state change takes place. This can lead to business logic vulnerabilities."
                                )

                                issue = Issue(
                                    contract=call.node.contract_name,
                                    function_name=call.node.function_name,
                                    address=address,
                                    title="State change after external call",
                                    _type="Warning",
                                    description=description,
                                    bytecode=state.environment.code.bytecode,
                                    swc_id=REENTRANCY,
                                    gas_used=(
                                        state.mstate.min_gas_used,
                                        state.mstate.max_gas_used,
                                    ),
                                )
                                issues.append(issue)

        return issues


detector = ExternalCallModule()

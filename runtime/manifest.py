# What the Quiver runtime freezes in, besides MicroPython's own modules. build.sh stages it under
# build/stage first (stage.py): the loader with the runtime's version and board, the app as the board
# came with it (its fonts as a module), and main.py, which then runs instead of any main.py on the
# filesystem.
include("$(PORT_DIR)/boards/manifest.py")
package("quiver_boot", base_path="$(BOARD_DIR)/../../build/stage")
package("quiver", base_path="$(BOARD_DIR)/../../build/stage")
module("main.py", base_path="$(BOARD_DIR)/../../build/stage")

On the left panel, we should have projects and trees.
Each project revolves around one git repo. Each repo can have multiple trees.

The user may create a new project, by click on the new project button on the top sector of the side bar. By default the new project is am empty folder that we assign. The user may change that folder name into another local git folder or a github link. If the local folder is not a git project, we'd warn the user that this is not a git folder and a git repo will be created. Once the folder is set, we'll default where the head is to be the branch and commit id. The user may select anther branch and commit id.

the project name is automatically parsed by the folder name of that local folder or the repo name of remote repo. [backend need to be updated as well.] For empty projects, we'll auto summarize. The entry can be editted manually by the user.

Each tree is made from the tree node, a tree node is a specific repo+branch+commit_id, the commit id is the only id needed to define a tree node. The user may change the local dir's location, or change branch or change head, the tree node always start from a commit id.

There are two types of tree node, a chat tree node which does not changes the codebase; or a edit tree node where the code is modified and a commit is made.

New tree can be created by either clicking the add button on the sidebar next to the project name, or clicking the edit-nodes and select make it a new root. The user will be asked to give it a name. The edit-node should have different color than the chat-node so that people can tell them apart.

On the left sidebar, under each project, is each tree, default is the commit id. It will be auto summarized later.

On the chat window on the right, I want to deprecate the chat panel. I don't care about backward comptabiliy. Just delete the chat and clean the ui code.

On the tool calling animation, displaying all tool calling in one list is a bit too long, maybe just show the latest tools and collapse the history tools? (the user might still be able to uncollapse it for inspection but the default is collapsed) We don't have to remove the tool calling from response now that we are making the tool calling collapsable.

Once a response is complete, the node should have some indicator color that disappears after the user have focused on it.

For a free note (notes that have not been quoted by any nodes), we'll have the option to pin it to a node. The specifics I have not decided. You can do whatever makes sense to you and we'll see.







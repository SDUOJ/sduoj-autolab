from abc import abstractmethod

from ser.base_type import page


class baseModel:
    @abstractmethod
    def create(self, data):
        pass

    @abstractmethod
    def update_by_id(self, id_, data):
        pass


class baseQuery:
    @abstractmethod
    def get_info_by_id(self, id_):
        pass

    @abstractmethod
    def get_obj_by_id(self, id_):
        pass


class listQuery:
    @abstractmethod
    def get_info_list_by_ids(self, ids):
        pass


class pageQuery:
    @abstractmethod
    def get_list_info_by_page(self, pg: page, username, groups):
        pass
